"""SQLite store: revision-checked, idempotent, atomic decisions (SPEC examples 3, 4, 5)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from conftest import make_proposal
from job_right.domain import profile_digest
from job_right.store import STORE_FILENAME, CandidateMismatch, ProfileStore, UnknownProposal
from job_right.synthetic import INITIAL_PROFILE


def raw(store_path: Path, sql: str, *params):
    with sqlite3.connect(store_path) as conn:
        return conn.execute(sql, params).fetchall()


def test_seed_creates_revision_one(seeded_store):
    current = seeded_store.current()
    assert current.revision == 1
    assert current.profile == INITIAL_PROFILE
    assert len(seeded_store.jobs()) == 14 and len(seeded_store.labels()) == 8


def test_approve_writes_the_reviewed_candidate_byte_for_byte(seeded_store, data_dir):
    p = make_proposal(seeded_store, "p-approve", min_salary=160_000)
    assert p.status == "pending" and p.base_revision == 1
    # Pending proposals never touch the current profile.
    assert seeded_store.current().revision == 1

    outcome = seeded_store.decide("p-approve", "approve", reviewed_digest=p.proposed_digest)
    assert (outcome.status, outcome.applied_revision, outcome.duplicate) == ("approved", 2, False)
    current = seeded_store.current()
    assert current.revision == 2 and current.profile.min_salary == 160_000
    assert current.proposal_id == "p-approve"
    assert profile_digest(current.profile) == p.proposed_digest
    store_path = data_dir / STORE_FILENAME
    [(stored_revision,)] = raw(store_path, "SELECT profile FROM profile_revisions WHERE revision = 2")
    [(stored_proposed,)] = raw(store_path, "SELECT proposed FROM proposals WHERE id = 'p-approve'")
    assert stored_revision == stored_proposed


def test_reject_leaves_current_profile_untouched(seeded_store):
    p = make_proposal(seeded_store, "p-reject", work_modes=["remote"])
    outcome = seeded_store.decide(p.id, "reject")
    assert (outcome.status, outcome.applied_revision) == ("rejected", None)
    assert seeded_store.current().revision == 1
    assert seeded_store.current().profile == INITIAL_PROFILE
    assert seeded_store.proposal(p.id).status == "rejected"


def test_stale_approval_cannot_overwrite_a_newer_revision(seeded_store):
    a = make_proposal(seeded_store, "p-a", min_salary=160_000)
    b = make_proposal(seeded_store, "p-b", preferred_skills=["python", "postgres", "kubernetes", "rust"])
    assert a.base_revision == b.base_revision == 1

    assert seeded_store.decide(b.id, "approve", reviewed_digest=b.proposed_digest).status == "approved"
    outcome = seeded_store.decide(a.id, "approve", reviewed_digest=a.proposed_digest)
    assert outcome.status == "stale"
    assert outcome.applied_revision is None and outcome.current_revision == 2
    current = seeded_store.current()
    assert current.revision == 2
    assert current.profile.min_salary == 150_000  # A's change did not land
    assert "rust" in current.profile.preferred_skills  # B's change is intact
    assert seeded_store.proposal(a.id).status == "stale"
    # Stale is final: approving again does not rebase or apply it (SPEC: no auto-rebase).
    again = seeded_store.decide(a.id, "approve", reviewed_digest=a.proposed_digest)
    assert (again.status, again.duplicate) == ("stale", True)
    assert seeded_store.current().revision == 2


def test_duplicate_and_conflicting_decisions_keep_the_first(seeded_store):
    p = make_proposal(seeded_store, "p-dup", min_salary=155_000)
    first = seeded_store.decide(p.id, "approve", reviewed_digest=p.proposed_digest)
    second = seeded_store.decide(p.id, "approve", reviewed_digest=p.proposed_digest)
    third = seeded_store.decide(p.id, "reject")
    assert (first.status, first.duplicate) == ("approved", False)
    assert (second.status, second.duplicate, second.applied_revision) == ("approved", True, 2)
    assert (third.status, third.duplicate) == ("approved", True)
    assert [r.revision for r in seeded_store.revisions()] == [1, 2]

    q = make_proposal(seeded_store, "p-rej-first", min_salary=170_000)
    assert seeded_store.decide(q.id, "reject").status == "rejected"
    late = seeded_store.decide(q.id, "approve", reviewed_digest=q.proposed_digest)
    assert (late.status, late.duplicate) == ("rejected", True)
    assert [r.revision for r in seeded_store.revisions()] == [1, 2]


def test_approval_must_carry_the_reviewed_digest(seeded_store):
    p = make_proposal(seeded_store, "p-digest", min_salary=165_000)
    with pytest.raises(CandidateMismatch):
        seeded_store.decide(p.id, "approve", reviewed_digest="0" * 64)
    with pytest.raises(ValueError):
        seeded_store.decide(p.id, "approve")
    assert seeded_store.proposal(p.id).status == "pending"
    assert seeded_store.current().revision == 1


def test_unknown_proposal(seeded_store):
    with pytest.raises(UnknownProposal):
        seeded_store.decide("nope", "reject")


def test_recording_a_proposal_twice_keeps_the_first_candidate(seeded_store):
    first = make_proposal(seeded_store, "p-once", min_salary=160_000)
    retried = make_proposal(seeded_store, "p-once", min_salary=999_000)
    assert retried.proposed == first.proposed
    assert retried.proposed_digest == first.proposed_digest
    assert len(seeded_store.proposals()) == 1


def test_failure_inside_the_decision_rolls_everything_back(seeded_store, data_dir):
    p = make_proposal(seeded_store, "p-atomic", min_salary=160_000)
    store_path = data_dir / STORE_FILENAME
    with sqlite3.connect(store_path) as conn:
        conn.execute(
            "CREATE TRIGGER injected_failure BEFORE UPDATE OF status ON proposals "
            "WHEN NEW.status = 'approved' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    with pytest.raises(sqlite3.DatabaseError):
        seeded_store.decide(p.id, "approve", reviewed_digest=p.proposed_digest)
    assert seeded_store.current().revision == 1
    assert raw(store_path, "SELECT COUNT(*) FROM profile_revisions") == [(1,)]
    assert seeded_store.proposal(p.id).status == "pending"


@pytest.mark.parametrize("decisions", [("approve", "approve"), ("approve", "reject"), ("reject", "approve")])
def test_racing_decisions_from_separate_connections_apply_at_most_once(seeded_store, data_dir, decisions):
    store_path = data_dir / STORE_FILENAME
    for round_no in range(10):
        p = make_proposal(seeded_store, f"p-race-{round_no}", min_salary=150_000 + 1_000 * (round_no + 1))
        barrier = threading.Barrier(len(decisions))
        results, errors = [], []

        def decide(decision: str, proposal=p, barrier=barrier, results=results, errors=errors):
            store = ProfileStore(store_path)
            try:
                barrier.wait()
                digest = proposal.proposed_digest if decision == "approve" else None
                results.append(store.decide(proposal.id, decision, reviewed_digest=digest))
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                store.close()

        threads = [threading.Thread(target=decide, args=(d,)) for d in decisions]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert sorted(r.duplicate for r in results) == [False, True]
        winner = next(r for r in results if not r.duplicate)
        assert all(r.status == winner.status for r in results)
        current = seeded_store.current()
        if winner.status == "approved":
            assert current.proposal_id == p.id
            assert profile_digest(current.profile) == p.proposed_digest
        else:
            assert current.proposal_id != p.id
    revisions = seeded_store.revisions()
    assert len({r.proposal_id for r in revisions}) == len(revisions)  # one revision per proposal at most
