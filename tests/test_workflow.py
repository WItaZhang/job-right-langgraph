"""LangGraph workflow: interrupt, cross-process resume, approve/reject, stale, duplicates, crash retry, races.

Covers SPEC examples 1-9 through the real graph and SQLite checkpointer.
"""

from __future__ import annotations

import threading
import warnings

import pytest

from conftest import make_proposal, profile_with, run_cli, run_cli_json
from job_right.chat_models import ChatModelChoice
from job_right.domain import profile_digest
from job_right.fixture_model import FixtureChatModel
from job_right.graph import ProposalIdInUse, Workflow
from job_right.store import STORE_FILENAME, ProfileStore
from job_right.synthetic import INITIAL_PROFILE

REMOTE_ONLY = "Only show me remote roles."
REMOTE_KEEP_FERNHILL = "Only show me remote roles, but keep Fernhill Freight's hybrid roles."
RUST = "Add Rust to my preferred skills."


def test_proposal_pauses_at_review_and_persists_only_a_pending_proposal(workflow, seeded_store):
    # SPEC example 1.
    result = workflow.propose(REMOTE_ONLY, proposal_id="p-remote")
    assert result.outcome is None
    proposal = result.proposal
    assert proposal.id == "p-remote" and proposal.status == "pending" and proposal.base_revision == 1
    assert proposal.current == INITIAL_PROFILE
    assert proposal.proposed == profile_with(INITIAL_PROFILE, work_modes=["remote"])
    assert [row.job_id for row in proposal.replay.wanted_newly_hidden] == ["jr-101"]
    assert "Fernhill Freight" in proposal.replay.wanted_newly_hidden[0].after.reasons[0].fix
    assert proposal.proposed_digest == profile_digest(proposal.proposed)

    assert [r.revision for r in seeded_store.revisions()] == [1]
    assert [(p.id, p.status) for p in seeded_store.proposals()] == [("p-remote", "pending")]
    assert workflow.pending("p-remote") == proposal


def test_approve_in_a_new_os_process_resumes_from_sqlite(seeded_store, data_dir):
    # SPEC example 2: propose and approve are separate `job-right` processes.
    proposed = run_cli_json(data_dir, "propose", "--id", "p-xproc", REMOTE_KEEP_FERNHILL)
    assert proposed["proposal"]["status"] == "pending"
    assert seeded_store.current().revision == 1

    with Workflow(data_dir) as observer:  # yet another connection sees the paused thread
        pending = observer.pending("p-xproc")
    assert pending is not None and pending.proposed_digest == proposed["proposal"]["proposed_digest"]

    outcome = run_cli_json(data_dir, "approve", "p-xproc")
    assert (outcome["status"], outcome["applied_revision"], outcome["duplicate"]) == ("approved", 2, False)
    current = seeded_store.current()
    assert current.revision == 2
    assert current.profile.model_dump(mode="json") == proposed["proposal"]["proposed"]
    assert profile_digest(current.profile) == proposed["proposal"]["proposed_digest"]


def test_reject_in_a_new_process_leaves_the_profile_untouched(seeded_store, data_dir):
    # SPEC example 3.
    run_cli(data_dir, "propose", "--id", "p-no", REMOTE_ONLY)
    outcome = run_cli_json(data_dir, "reject", "p-no")
    assert (outcome["status"], outcome["applied_revision"]) == ("rejected", None)
    assert seeded_store.current().revision == 1
    assert seeded_store.current().profile == INITIAL_PROFILE
    assert seeded_store.proposal("p-no").status == "rejected"


def test_stale_approval_through_the_graph(workflow, seeded_store):
    # SPEC example 4: A and B both start from revision 1; B lands first.
    a = workflow.propose("Raise my salary floor to $160k.", proposal_id="p-a").proposal
    b = workflow.propose(RUST, proposal_id="p-b").proposal
    assert a.base_revision == b.base_revision == 1
    assert workflow.decide("p-b", "approve").status == "approved"
    stale = workflow.decide("p-a", "approve")
    assert stale.status == "stale" and stale.applied_revision is None and stale.current_revision == 2
    current = seeded_store.current()
    assert current.revision == 2 and current.profile == b.proposed
    assert current.profile.min_salary == 150_000

    # The user re-proposes against the new revision; nothing is rebased automatically.
    again = workflow.propose("Raise my salary floor to $160k.", proposal_id="p-a2").proposal
    assert again.base_revision == 2
    assert workflow.decide("p-a2", "approve").applied_revision == 3
    assert seeded_store.current().profile == profile_with(b.proposed, min_salary=160_000)


def test_duplicate_resume_does_not_apply_twice(seeded_store, data_dir):
    # SPEC example 5: the same approval runs twice in separate processes, then a conflicting reject.
    run_cli(data_dir, "propose", "--id", "p-twice", RUST)
    first = run_cli_json(data_dir, "approve", "p-twice")
    second = run_cli_json(data_dir, "approve", "p-twice")
    late_reject = run_cli_json(data_dir, "reject", "p-twice")
    assert (first["status"], first["duplicate"]) == ("approved", False)
    assert (second["status"], second["duplicate"], second["applied_revision"]) == ("approved", True, 2)
    assert (late_reject["status"], late_reject["duplicate"]) == ("approved", True)
    assert [r.revision for r in seeded_store.revisions()] == [1, 2]


@pytest.mark.parametrize("decisions", [("approve", "approve"), ("approve", "reject")])
def test_racing_resumes_persist_only_the_reviewed_candidate(seeded_store, data_dir, decisions):
    # SPEC example 5: two workflows (separate checkpoint and store connections) resume the same interrupt.
    for round_no in range(3):
        with Workflow(data_dir) as wf:
            reviewed = wf.propose(f"Raise my salary floor to ${151 + round_no}k.").proposal
        assert reviewed is not None
        barrier = threading.Barrier(len(decisions))
        outcomes, errors = [], []

        def resume(decision, proposal_id=reviewed.id, barrier=barrier, outcomes=outcomes, errors=errors):
            try:
                with Workflow(data_dir) as wf:
                    barrier.wait()
                    outcomes.append(wf.decide(proposal_id, decision))
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=resume, args=(d,)) for d in decisions]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert sorted(o.duplicate for o in outcomes) == [False, True]
        recorded = seeded_store.proposal(reviewed.id)
        assert all(o.status == recorded.status for o in outcomes)
        if recorded.status == "approved":
            current = seeded_store.current()
            assert current.proposal_id == reviewed.id
            assert profile_digest(current.profile) == reviewed.proposed_digest
    revisions = seeded_store.revisions()
    assert len({r.proposal_id for r in revisions}) == len(revisions)


class SimulatedCrash(RuntimeError):
    pass


class CrashAfterCommit(ProfileStore):
    """Commits the decision, then dies before LangGraph can checkpoint the commit node."""

    def decide(self, *args, **kwargs):
        super().decide(*args, **kwargs)
        raise SimulatedCrash("process died after the SQLite commit")


def test_retry_after_a_crash_between_commit_and_checkpoint_does_not_apply_twice(seeded_store, data_dir):
    # SPEC example 6.
    with Workflow(data_dir) as wf:
        proposal = wf.propose(RUST, proposal_id="p-crash").proposal
    crashing = Workflow(data_dir, store=CrashAfterCommit(data_dir / STORE_FILENAME))
    with pytest.raises(SimulatedCrash):
        crashing.decide("p-crash", "approve")
    crashing.close()
    assert seeded_store.current().revision == 2  # the commit itself did happen

    with Workflow(data_dir) as fresh:  # a new process picks the thread up again
        assert fresh.pending("p-crash") is None  # the interrupt was already answered
        outcome = fresh.decide("p-crash", "approve")
    assert outcome.status == "approved" and outcome.applied_revision == 2 and outcome.duplicate
    assert [r.revision for r in seeded_store.revisions()] == [1, 2]
    assert profile_digest(seeded_store.current().profile) == proposal.proposed_digest


def test_store_decided_before_the_interrupt_was_resumed(workflow, seeded_store):
    # SPEC example 6, other ordering: the store already holds the decision while the checkpoint still waits.
    proposal = workflow.propose(RUST, proposal_id="p-early").proposal
    seeded_store.decide("p-early", "approve", reviewed_digest=proposal.proposed_digest)
    outcome = workflow.decide("p-early", "approve")
    assert outcome.status == "approved" and outcome.duplicate
    assert [r.revision for r in seeded_store.revisions()] == [1, 2]


def test_multi_turn_changes_with_reversal_keep_unrelated_fields_and_employer_exceptions(
    workflow, seeded_store
):
    # SPEC example 7 end to end: propose -> review -> approve, three times, each on the latest revision.
    expected = INITIAL_PROFILE
    for feedback, change in [
        (REMOTE_ONLY, {"work_modes": ["remote"]}),
        (RUST, {"preferred_skills": ["python", "postgres", "kubernetes", "rust"]}),
        ("Hybrid roles are fine again.", {"work_modes": ["remote", "hybrid"]}),
    ]:
        proposal = workflow.propose(feedback).proposal
        expected = profile_with(expected, **change)
        assert proposal.proposed == expected
        assert workflow.decide(proposal.id, "approve").status == "approved"
        current = seeded_store.current().profile
        assert current == expected
        assert current.employer_exceptions == INITIAL_PROFILE.employer_exceptions
    assert [r.revision for r in seeded_store.revisions()] == [1, 2, 3, 4]
    assert seeded_store.current().profile.work_modes == INITIAL_PROFILE.work_modes  # reversal restored it


def test_feedback_without_change_ends_without_interrupt(workflow, seeded_store):
    # SPEC example 8.
    result = workflow.propose("Tell me a joke about recruiters.", proposal_id="p-joke")
    assert result.proposal is None
    assert result.outcome.status == "no_change"
    assert seeded_store.proposals() == []
    assert workflow.pending("p-joke") is None
    assert seeded_store.current().revision == 1


def test_invalid_candidate_is_never_proposed(workflow, seeded_store):
    # SPEC example 9.
    fixture = FixtureChatModel()
    result = workflow.propose(
        "Clear my work modes.", chat=ChatModelChoice(model=fixture, label="fixture", live=False)
    )
    assert result.proposal is None
    assert result.outcome.status in {"no_change", "invalid"}
    assert seeded_store.proposals() == []
    assert seeded_store.current().profile == INITIAL_PROFILE


def test_proposal_ids_are_single_use(workflow):
    workflow.propose(RUST, proposal_id="p-once")
    with pytest.raises(ProposalIdInUse):
        workflow.propose(REMOTE_ONLY, proposal_id="p-once")


def test_resume_needs_no_model_and_raises_no_checkpoint_warnings(seeded_store, data_dir):
    with Workflow(data_dir) as wf:
        wf.propose(RUST, proposal_id="p-quiet")
    with warnings.catch_warnings():
        # LangGraph warns "Deserializing unregistered type ..." for checkpoint types outside the allowlist.
        warnings.filterwarnings("error", message=r".*[Dd]eserializ.*")
        with Workflow(data_dir) as wf:
            assert wf.pending("p-quiet") is not None
            assert wf.decide("p-quiet", "approve").status == "approved"


def test_proposals_recorded_outside_the_graph_cannot_be_resumed(workflow, seeded_store):
    make_proposal(seeded_store, "p-manual", min_salary=151_000)
    with pytest.raises(LookupError):
        workflow.decide("p-manual", "approve")
