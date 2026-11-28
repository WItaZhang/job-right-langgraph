"""LangMem is the real proposal stage, driven offline by the deterministic fixture (SPEC examples 7, 8, 9)."""

from __future__ import annotations

import pytest
from langmem.knowledge.extraction import ExtractedMemory

from conftest import profile_with
from job_right.fixture_model import FixtureChatModel
from job_right.profile_memory import (
    PROFILE_MEMORY_ID,
    InvalidCandidate,
    propose_profile_update,
    validate_candidate,
)
from job_right.synthetic import INITIAL_PROFILE


def test_fixture_answers_langmem_patchdoc_tool_call_and_unrelated_fields_survive():
    model = FixtureChatModel()
    update = propose_profile_update(model, INITIAL_PROFILE, "Only show me remote roles.")

    # The call went through LangMem's memory manager into trustcall's update-only PatchDoc tool.
    (call,) = model.calls
    assert call["tools"] == ["PatchDoc"]
    assert call["tool_choice"] == "PatchDoc"
    assert "memory subroutine" in call["prompt"]
    assert f"<instance id={PROFILE_MEMORY_ID}" in call["prompt"]
    assert "Only show me remote roles." in call["prompt"]

    assert update.candidate == profile_with(INITIAL_PROFILE, work_modes=["remote"])
    assert [c.field for c in update.changes] == ["work_modes"]
    assert update.changes[0].before == ["remote", "hybrid"] and update.changes[0].after == ["remote"]


def test_employer_exception_is_added_alongside_a_global_change():
    update = propose_profile_update(
        FixtureChatModel(),
        INITIAL_PROFILE,
        "Only show me remote roles, but keep Fernhill Freight's hybrid roles.",
    )
    assert update.candidate.work_modes == ["remote"]
    exceptions = update.candidate.model_dump(mode="json")["employer_exceptions"]
    assert exceptions == {
        "cobalt-orchard-health": {"work_modes": ["remote", "hybrid", "onsite"]},
        "fernhill-freight": {"work_modes": ["remote", "hybrid"]},
    }
    assert sorted(c.field for c in update.changes) == ["employer_exceptions.fernhill-freight", "work_modes"]


def test_changes_across_turns_including_a_reversal_keep_unrelated_fields_and_exceptions():
    # SPEC example 7 at the LangMem level: each turn feeds the previous typed profile back in.
    model = FixtureChatModel()
    turn1 = propose_profile_update(model, INITIAL_PROFILE, "Only show me remote roles.").candidate
    turn2 = propose_profile_update(model, turn1, "Add Rust to my preferred skills.").candidate
    turn3 = propose_profile_update(model, turn2, "Hybrid roles are fine again.").candidate

    assert turn1 == profile_with(INITIAL_PROFILE, work_modes=["remote"])
    assert turn2 == profile_with(turn1, preferred_skills=["python", "postgres", "kubernetes", "rust"])
    assert turn3 == profile_with(turn2, work_modes=["remote", "hybrid"])
    for profile in (turn1, turn2, turn3):
        assert profile.employer_exceptions == INITIAL_PROFILE.employer_exceptions
        assert profile.min_salary == INITIAL_PROFILE.min_salary
        assert profile.locations == INITIAL_PROFILE.locations
    assert len(model.calls) == 3 and all(c["tool_choice"] == "PatchDoc" for c in model.calls)


def test_salary_floor_parsing():
    update = propose_profile_update(FixtureChatModel(), INITIAL_PROFILE, "Raise my salary floor to $160k.")
    assert update.candidate.min_salary == 160_000
    assert [c.field for c in update.changes] == ["min_salary"]


def test_feedback_without_a_preference_change_yields_no_changes():
    # SPEC example 8 at the LangMem level.
    model = FixtureChatModel()
    update = propose_profile_update(model, INITIAL_PROFILE, "Tell me a joke about recruiters.")
    assert update.changes == []
    assert update.candidate == INITIAL_PROFILE
    assert len(model.calls) == 1


def test_invalid_candidate_is_repaired_or_dropped_by_trustcall_never_proposed():
    # SPEC example 9: emptying work_modes violates the schema; trustcall asks the model to repair it,
    # the fixture cannot, and the invalid candidate never becomes a change.
    model = FixtureChatModel()
    update = propose_profile_update(model, INITIAL_PROFILE, "Clear my work modes.")
    assert update.changes == []
    assert update.candidate == INITIAL_PROFILE
    assert any("PatchFunctionErrors" in c["tools"] for c in model.calls[1:])


def test_validate_candidate_rejects_wrong_identity_and_invalid_content():
    good = INITIAL_PROFILE.model_dump(mode="json")
    with pytest.raises(InvalidCandidate):
        validate_candidate([], INITIAL_PROFILE)
    with pytest.raises(InvalidCandidate):
        validate_candidate([ExtractedMemory(id="someone-else", content=good)], INITIAL_PROFILE)
    with pytest.raises(InvalidCandidate):
        validate_candidate(
            [ExtractedMemory(id=PROFILE_MEMORY_ID, content={**good, "work_modes": []})], INITIAL_PROFILE
        )
    with pytest.raises(InvalidCandidate):
        validate_candidate(
            [ExtractedMemory(id=PROFILE_MEMORY_ID, content={**good, "favourite_colour": "teal"})],
            INITIAL_PROFILE,
        )
    update = validate_candidate(
        [ExtractedMemory(id=PROFILE_MEMORY_ID, content={**good, "min_salary": 175_000})], INITIAL_PROFILE
    )
    assert update.candidate.min_salary == 175_000 and [c.field for c in update.changes] == ["min_salary"]
