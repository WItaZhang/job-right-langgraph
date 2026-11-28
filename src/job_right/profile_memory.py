"""The proposal stage: LangMem's memory manager updates a copy of the current typed profile.

The previous profile is LangMem's single existing memory; the feedback arrives as LangChain Core
messages. With inserts and deletes disabled, trustcall can only answer with ``PatchDoc`` JSON patches
against that one document, and validates the patched result against ``JobSeekerProfile`` (asking the
model to repair it if invalid). The validated candidate minus the current profile is the typed edit.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ValidationError

from .domain import JobSeekerProfile, ProfileChange, diff_profiles

PROFILE_MEMORY_ID = "job-seeker-profile"

PROFILE_INSTRUCTIONS = (
    "You maintain one job seeker's search preferences as a single JobSeekerProfile document. "
    "Apply only what the new feedback asks for: change the fields it is about and leave every other field, "
    "including employer exceptions, exactly as it is. A preference that reverses an earlier one replaces it. "
    "Employer-specific statements belong in employer_exceptions, keyed by the employer slug. "
    "If the feedback does not change a preference, return an empty list of patches.\n\n"
    "JobSeekerProfile JSON schema:\n"
    + json.dumps(JobSeekerProfile.model_json_schema(), separators=(",", ":"))
)

FEEDBACK_PROMPT = ChatPromptTemplate.from_messages([("human", "Feedback on my recommendations: {feedback}")])


class ProfileUpdate(BaseModel):
    candidate: JobSeekerProfile
    changes: list[ProfileChange]


class InvalidCandidate(ValueError):
    """LangMem's output is not exactly one valid JobSeekerProfile with the expected identity."""


def run_profile_manager(model: BaseChatModel, current: JobSeekerProfile, feedback: str) -> list[Any]:
    """Run LangMem's memory manager once, with the current profile as the only existing memory."""
    from langmem import create_memory_manager

    manager = create_memory_manager(
        model,
        schemas=[JobSeekerProfile],
        instructions=PROFILE_INSTRUCTIONS,
        enable_inserts=False,
        enable_updates=True,
        enable_deletes=False,
    )
    return manager.invoke(
        {
            "messages": FEEDBACK_PROMPT.format_messages(feedback=feedback),
            "existing": [(PROFILE_MEMORY_ID, "JobSeekerProfile", current.model_dump(mode="json"))],
            "max_steps": 1,
        }
    )


def validate_candidate(memories: Sequence[Any], current: JobSeekerProfile) -> ProfileUpdate:
    """Accept only one memory, with the profile's identity, that validates as a JobSeekerProfile."""
    if len(memories) != 1:
        raise InvalidCandidate(f"expected exactly one profile memory, got {len(memories)}")
    memory = memories[0]
    if memory.id != PROFILE_MEMORY_ID:
        raise InvalidCandidate(f"expected memory {PROFILE_MEMORY_ID!r}, got {memory.id!r}")
    content = (
        memory.content.model_dump(mode="json") if isinstance(memory.content, BaseModel) else memory.content
    )
    try:
        candidate = JobSeekerProfile.model_validate(content)
    except ValidationError as exc:
        raise InvalidCandidate(str(exc)) from exc
    return ProfileUpdate(candidate=candidate, changes=diff_profiles(current, candidate))


def propose_profile_update(model: BaseChatModel, current: JobSeekerProfile, feedback: str) -> ProfileUpdate:
    return validate_candidate(run_profile_manager(model, current, feedback), current)
