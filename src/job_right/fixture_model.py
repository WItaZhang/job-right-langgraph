"""A deterministic, offline stand-in for a tool-calling chat model. It is NOT a language model.

LangMem's memory manager drives trustcall, which asks the bound chat model to call ``PatchDoc`` with
JSON Patch operations against the existing profile. This fixture answers that call using a handful of
regular-expression rules over synthetic phrasings, and it reads the current profile from the prompt,
exactly as a real model would see it. Everything around it is the real code path: LangMem, trustcall
validation and repair, the LangGraph workflow, replay and the SQLite commit.

Recognised phrasings (case-insensitive):
  "only ... remote" / "remote-only"           -> work_modes = ["remote"]
  "hybrid ... fine/ok/okay/acceptable"        -> add "hybrid" to work_modes
  "keep <Employer>'s hybrid roles"            -> employer exception allowing hybrid at that employer
  "salary/floor/at least/minimum ... $160k"   -> min_salary = 160000
  "add <skill> to my (preferred) skills"      -> append to preferred_skills
  "drop/remove <skill> from my skills"        -> remove from preferred_skills
  "no (more) <industry> companies/jobs/roles" -> append to excluded_industries
  "clear my work modes"                       -> work_modes = [] (invalid on purpose: exercises repair)
Anything else yields an empty patch list, i.e. "no change".
"""

from __future__ import annotations

import ast
import copy
import re
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from .domain import WORK_MODES, employer_key

FIXTURE_LABEL = "fixture (deterministic rules, offline; not an LLM)"

_INSTANCE = re.compile(r'<instance id=(?P<id>\S+) schema_type="[^"]*">\n(?P<doc>.*?)\n</instance>', re.S)
_SESSION = re.compile(r"<session_[^>]*>(?P<body>.*?)</session_[^>]*>", re.S)
_MESSAGE_HEADER = re.compile(r"^=+ .*? Message =+$", re.M)
_REPAIR_TARGET = re.compile(r"json_doc_id=\[(?P<id>[^\]]+)\]")

_REMOTE_ONLY = re.compile(r"\b(?:only|just)\b[^.;]*\bremote\b|\bremote[- ]only\b", re.I)
_HYBRID_OK = re.compile(r"\bhybrid\b[^.;]*\b(?:fine|ok|okay|acceptable)\b", re.I)
_KEEP_EMPLOYER_HYBRID = re.compile(r"\bkeep ([\w&-]+(?:\s+(?:&\s+)?[\w&-]+)*?)['’]s hybrid roles", re.I)
_SALARY_CONTEXT = re.compile(r"\b(?:salary|floor|pay|paying|at least|minimum|under)\b", re.I)
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)\s*(k)?\b|\b(\d+)\s*(k)\b", re.I)
_ADD_SKILL = re.compile(r"\badd ([\w+#.-]+?) to (?:my )?(?:preferred )?skills\b", re.I)
_DROP_SKILL = re.compile(r"\b(?:drop|remove) ([\w+#.-]+?) from (?:my )?(?:preferred )?skills\b", re.I)
_EXCLUDE_INDUSTRY = re.compile(r"\bno (?:more )?([a-z]+) (?:companies|employers|jobs|roles)\b", re.I)
_CLEAR_MODES = re.compile(r"\bclear my work modes\b", re.I)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)


def _money(text: str) -> int | None:
    match = _MONEY.search(text)
    if not match:
        return None
    digits, k = (match[1], match[2]) if match[1] else (match[3], match[4])
    amount = int(digits.replace(",", ""))
    return amount * 1000 if k else amount


class _Patcher:
    """Collects JSON Patch operations while keeping a working copy of the document in sync."""

    def __init__(self, doc: dict[str, Any]):
        self.doc = copy.deepcopy(doc)
        self.patches: list[dict[str, Any]] = []
        self.notes: list[str] = []

    def replace(self, field: str, value: Any, note: str) -> None:
        self.patches.append({"op": "replace", "path": f"/{field}", "value": value})
        self.doc[field] = value
        self.notes.append(note)

    def append(self, field: str, value: Any, note: str) -> None:
        self.patches.append({"op": "add", "path": f"/{field}/-", "value": value})
        self.doc[field] = [*self.doc[field], value]
        self.notes.append(note)

    def remove(self, field: str, index: int, note: str) -> None:
        # trustcall's JsonPatch schema always carries a value; jsonpatch ignores it for "remove".
        self.patches.append({"op": "remove", "path": f"/{field}/{index}", "value": None})
        self.doc[field] = [v for i, v in enumerate(self.doc[field]) if i != index]
        self.notes.append(note)

    def put(self, field: str, key: str, value: Any, note: str) -> None:
        op = "replace" if key in self.doc[field] else "add"
        self.patches.append({"op": op, "path": f"/{field}/{key}", "value": value})
        self.doc[field] = {**self.doc[field], key: value}
        self.notes.append(note)


def _rules(feedback: str, patcher: _Patcher) -> None:
    doc = patcher.doc
    if _REMOTE_ONLY.search(feedback) and doc["work_modes"] != ["remote"]:
        patcher.replace("work_modes", ["remote"], "work_modes -> remote only")
    if _HYBRID_OK.search(feedback) and "hybrid" not in patcher.doc["work_modes"]:
        patcher.append("work_modes", "hybrid", "allow hybrid again")
    for name in _KEEP_EMPLOYER_HYBRID.findall(feedback):
        key = employer_key(name)
        existing = patcher.doc["employer_exceptions"].get(key, {})
        allowed = {*existing.get("work_modes", patcher.doc["work_modes"]), "hybrid"}
        modes = [mode for mode in WORK_MODES if mode in allowed]
        patcher.put("employer_exceptions", key, {"work_modes": modes}, f"exception for {name}")
    if _SALARY_CONTEXT.search(feedback):
        amount = _money(feedback)
        if amount is not None and amount != patcher.doc["min_salary"]:
            patcher.replace("min_salary", amount, f"min_salary -> {amount}")
    for skill in (s.lower() for s in _ADD_SKILL.findall(feedback)):
        if skill not in patcher.doc["preferred_skills"]:
            patcher.append("preferred_skills", skill, f"prefer {skill}")
    for skill in (s.lower() for s in _DROP_SKILL.findall(feedback)):
        if skill in patcher.doc["preferred_skills"]:
            patcher.remove("preferred_skills", patcher.doc["preferred_skills"].index(skill), f"drop {skill}")
    for industry in (i.lower() for i in _EXCLUDE_INDUSTRY.findall(feedback)):
        if industry not in patcher.doc["excluded_industries"]:
            patcher.append("excluded_industries", industry, f"exclude {industry}")
    if _CLEAR_MODES.search(feedback):
        patcher.replace("work_modes", [], "clear work_modes")


class FixtureChatModel(BaseChatModel):
    """Deterministic offline fixture that answers LangMem/trustcall tool calls. Not a language model."""

    calls: list[dict[str, Any]] = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "job-right-fixture"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        return self.bind(
            tools=[convert_to_openai_tool(tool) for tool in tools], tool_choice=tool_choice, **kwargs
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        tools = [tool["function"]["name"] for tool in kwargs.get("tools", [])]
        prompt = "\n\n".join(_text(message.content) for message in messages)
        self.calls.append({"tools": tools, "tool_choice": kwargs.get("tool_choice"), "prompt": prompt})

        if "PatchDoc" in tools:
            call = self._patch_doc(prompt)
        elif "PatchFunctionErrors" in tools:
            target = _REPAIR_TARGET.search(prompt)
            call = {
                "name": "PatchFunctionErrors",
                "args": {
                    "json_doc_id": target["id"] if target else "",
                    "planned_edits": "The fixture has no rule to repair this candidate.",
                    "patches": [],
                },
            }
        else:
            reply = AIMessage(content="The fixture only answers LangMem profile patch requests.")
            return ChatResult(generations=[ChatGeneration(message=reply)])
        call["id"] = f"fixture-call-{len(self.calls)}"
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])

    @staticmethod
    def _patch_doc(prompt: str) -> dict[str, Any]:
        instance = _INSTANCE.search(prompt)
        if instance is None:
            raise ValueError("fixture expected trustcall's <instance> block for the existing profile")
        session = _SESSION.search(prompt)
        feedback = _MESSAGE_HEADER.sub("", session["body"] if session else prompt).strip()
        patcher = _Patcher(ast.literal_eval(instance["doc"]))
        _rules(feedback, patcher)
        return {
            "name": "PatchDoc",
            "args": {
                "json_doc_id": instance["id"],
                "planned_edits": "; ".join(patcher.notes) or "No preference change requested.",
                "patches": patcher.patches,
            },
        }
