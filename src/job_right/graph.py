"""Durable preference review. Only JSON data crosses a checkpoint boundary."""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from filelock import FileLock
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from langsmith import tracing_context
from pydantic import BaseModel

from .chat_models import ChatModelChoice, fixture_choice
from .domain import JobSeekerProfile, diff_profiles
from .errors import CandidateMismatch, InvalidProposalId, JobRightError, NothingToDecide, ProposalIdInUse
from .profile_memory import InvalidCandidate, propose_profile_update
from .ranking import recommend
from .replay import replay
from .store import STORE_FILENAME, Decision, Outcome, ProfileStore, Proposal
from .synthetic import INITIAL_PROFILE, SYNTHETIC_JOBS, SYNTHETIC_LABELS


class State(TypedDict, total=False):
    id: str
    feedback: str
    model_label: str
    base_revision: int
    current: dict
    proposed: dict
    changes: list
    replay: dict
    proposed_digest: str
    decision: Decision
    outcome: dict


@dataclass
class Context:
    store: ProfileStore
    chat: ChatModelChoice | None


class ReviewResult(BaseModel):
    proposal: Proposal | None = None
    outcome: Outcome | None = None


def load_profile(state: State, runtime: Runtime[Context]):
    current = runtime.context.store.current()
    return {"base_revision": current.revision, "current": current.profile.model_dump(mode="json")}


def propose_update(state: State, runtime: Runtime[Context]):
    current = JobSeekerProfile.model_validate(state["current"])
    try:
        update = propose_profile_update(runtime.context.chat.model, current, state["feedback"])
    except InvalidCandidate as exc:
        return {"outcome": _stopped(state, "invalid", str(exc))}
    return {"proposed": update.candidate.model_dump(mode="json")}


def _stopped(state, status, message):
    return Outcome(
        proposal_id=state["id"], status=status, current_revision=state["base_revision"], message=message
    ).model_dump(mode="json")


def validate_candidate(state: State):
    if "outcome" in state:
        return {}
    changes = diff_profiles(
        JobSeekerProfile.model_validate(state["current"]), JobSeekerProfile.model_validate(state["proposed"])
    )
    if not changes:
        return {"outcome": _stopped(state, "no_change", "No preference changed. Try a different example.")}
    return {"changes": [change.model_dump(mode="json") for change in changes]}


def replay_labels(state: State, runtime: Runtime[Context]):
    store = runtime.context.store
    report = replay(
        JobSeekerProfile.model_validate(state["current"]),
        JobSeekerProfile.model_validate(state["proposed"]),
        store.jobs(),
        store.labels(),
    )
    return {"replay": report.model_dump(mode="json")}


def record_proposal(state: State, runtime: Runtime[Context]):
    proposal = runtime.context.store.record_proposal(Proposal.model_validate(state))
    return {"proposed_digest": proposal.proposed_digest}


def human_review(state: State):
    answer = interrupt({"proposal_id": state["id"], "proposed_digest": state["proposed_digest"]})
    if answer["digest"] != state["proposed_digest"]:
        raise CandidateMismatch("The review has changed. Open the proposal again.")
    return {"decision": answer["decision"]}


def commit_decision(state: State, runtime: Runtime[Context]):
    outcome = runtime.context.store.decide(state["id"], state["decision"], state["proposed_digest"])
    return {"outcome": outcome.model_dump(mode="json")}


def _build_graph():
    graph = StateGraph(State, context_schema=Context)
    graph.add_sequence([load_profile, propose_update, validate_candidate])
    graph.add_edge(START, "load_profile")
    graph.add_conditional_edges(
        "validate_candidate", lambda state: END if "outcome" in state else "replay_labels"
    )
    graph.add_sequence([replay_labels, record_proposal, human_review, commit_decision])
    graph.add_edge("commit_decision", END)
    return graph


_GRAPH = _build_graph()


class Workflow:
    """One connection pair per caller. Serialize writers to a single graph thread across processes."""

    def __init__(self, data_dir: Path, *, store: ProfileStore | None = None):
        self.data_dir = Path(data_dir)
        self.store = store or ProfileStore(self.data_dir / STORE_FILENAME)
        self._checkpoints = sqlite3.connect(
            self.data_dir / "checkpoints.sqlite", check_same_thread=False, timeout=30
        )
        self.graph = _GRAPH.compile(checkpointer=SqliteSaver(self._checkpoints))

    def close(self):
        self._checkpoints.close()
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def initialize(self):
        return self.store.seed(INITIAL_PROFILE, SYNTHETIC_JOBS, SYNTHETIC_LABELS)

    def recommendations(self):
        return recommend(self.store.current().profile, self.store.jobs())

    def _lock(self, proposal_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", proposal_id):
            raise InvalidProposalId("Use 1–80 letters, numbers, hyphens or underscores for the proposal ID.")
        locks = self.data_dir / "locks"
        locks.mkdir(exist_ok=True)
        return FileLock(locks / f"{proposal_id}.lock", timeout=60)

    @staticmethod
    def _config(proposal_id):
        return {"configurable": {"thread_id": proposal_id}}

    def _invoke(self, data, config, chat=None):
        enabled = None if os.environ.get("JOB_RIGHT_ALLOW_LANGSMITH") == "1" else False
        with tracing_context(enabled=enabled):
            return self.graph.invoke(
                data, config, context=Context(self.store, chat), durability="async" if chat else "sync"
            )

    def propose(self, feedback: str, proposal_id: str | None = None, chat: ChatModelChoice | None = None):
        proposal_id = proposal_id if proposal_id is not None else uuid4().hex
        if not feedback.strip():
            raise JobRightError("Enter a preference before requesting a review.")
        config = self._config(proposal_id)
        with self._lock(proposal_id):
            if self.graph.get_state(config).created_at or self.store.has_proposal(proposal_id):
                raise ProposalIdInUse(f"Proposal ID {proposal_id!r} is already in use.")
            chat = chat or fixture_choice()
            result = self._invoke(
                {"id": proposal_id, "feedback": feedback.strip(), "model_label": chat.label}, config, chat
            )
            if "outcome" in result:
                return ReviewResult(outcome=Outcome.model_validate(result["outcome"]))
            return ReviewResult(proposal=self.store.proposal(proposal_id))

    def pending(self, proposal_id: str):
        with self._lock(proposal_id):
            if not self.store.has_proposal(proposal_id):
                return None
            proposal = self.store.proposal(proposal_id)
            state = self.graph.get_state(self._config(proposal_id))
            return proposal if proposal.status == "pending" and state.next else None

    def decide(self, proposal_id: str, decision: Decision, reviewed_digest: str | None = None):
        if decision not in ("approve", "reject"):
            raise JobRightError("Choose approve or reject.")
        config = self._config(proposal_id)
        with self._lock(proposal_id):
            proposal = self.store.proposal(proposal_id)
            state = self.graph.get_state(config)
            # record_proposal can commit just before a process dies or before interrupt is checkpointed.
            if state.next in (("record_proposal",), ("human_review",)) and not state.interrupts:
                self._invoke(None, config)
                state = self.graph.get_state(config)
            if state.interrupts:
                digest = state.interrupts[0].value["proposed_digest"]
                if proposal.status == "pending" and (
                    digest != proposal.proposed_digest
                    or reviewed_digest is not None
                    and reviewed_digest != digest
                ):
                    raise CandidateMismatch(
                        "This review differs from the saved candidate. Reload the review."
                    )
                data: Any = Command(resume={"decision": decision, "digest": digest})
                route = "interrupt"
            elif state.next == ("commit_decision",):
                data, route = None, "commit_retry"
            elif state.values.get("outcome") and proposal.status != "pending":
                outcome = self.store.decide(proposal_id, decision, proposal.proposed_digest)
                return outcome.model_copy(update={"route": "recorded"})
            else:
                raise NothingToDecide(f"No review checkpoint exists for {proposal_id!r}.")
            result = self._invoke(data, config)
            return Outcome.model_validate({**result["outcome"], "route": route})
