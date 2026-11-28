"""Choose the LangChain Core chat model behind the LangMem proposal stage.

``fixture`` (the default) is the deterministic offline stand-in. Anything of the form
``provider:model`` is a user-supplied live model built by LangChain's ``init_chat_model``, using the
caller's own credentials (for example ``OPENAI_API_KEY``) or an OpenAI-compatible ``OPENAI_BASE_URL``.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from .errors import InvalidModelSpec
from .fixture_model import FIXTURE_LABEL, FixtureChatModel


@dataclass(frozen=True)
class ChatModelChoice:
    model: BaseChatModel
    label: str
    live: bool


def fixture_choice() -> ChatModelChoice:
    return ChatModelChoice(model=FixtureChatModel(), label=FIXTURE_LABEL, live=False)


def resolve_chat_model(spec: str | None) -> ChatModelChoice:
    if spec is None or spec.strip() in ("", "fixture"):
        return fixture_choice()
    provider, sep, name = spec.partition(":")
    if not (sep and provider and name):
        raise InvalidModelSpec(
            f"--model must be 'fixture' or 'provider:model' (for example openai:gpt-5-mini), got {spec!r}"
        )
    from langchain.chat_models import init_chat_model

    return ChatModelChoice(model=init_chat_model(spec), label=f"live: {spec}", live=True)
