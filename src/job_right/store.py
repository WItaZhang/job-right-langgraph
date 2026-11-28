"""SQLite app store: synthetic jobs and labels, the profile revision history, and review proposals.

`decide()` is the only way the current profile changes. It runs in one `BEGIN IMMEDIATE` transaction:
the first decision on a proposal wins, and an approval lands only while the proposal's base revision is
still the newest one. The new revision is a verbatim copy of the proposal's stored candidate text.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from .domain import Job, JobSeekerProfile, Label, ProfileChange, canonical_json, profile_digest
from .errors import AlreadyInitialized, CandidateMismatch, NotInitialized, UnknownProposal
from .materials import Packet
from .replay import ReplayReport

STORE_FILENAME = "job_right.sqlite"

Decision = Literal["approve", "reject"]
ProposalStatus = Literal["pending", "approved", "rejected", "stale"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS packets (
    id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    data TEXT NOT NULL,
    UNIQUE(target_id, revision)
);
CREATE TABLE IF NOT EXISTS labels (
    job_id TEXT PRIMARY KEY REFERENCES jobs (id),
    verdict TEXT NOT NULL CHECK (verdict IN ('wanted', 'rejected')),
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS profile_revisions (
    revision INTEGER PRIMARY KEY,
    profile TEXT NOT NULL,
    proposal_id TEXT UNIQUE REFERENCES proposals (id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    feedback TEXT NOT NULL,
    model_label TEXT NOT NULL,
    base_revision INTEGER NOT NULL REFERENCES profile_revisions (revision),
    current TEXT NOT NULL,
    proposed TEXT NOT NULL,
    changes TEXT NOT NULL,
    replay TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'stale')),
    applied_revision INTEGER REFERENCES profile_revisions (revision),
    created_at TEXT NOT NULL,
    decided_at TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Revision(BaseModel):
    revision: int
    profile: JobSeekerProfile
    proposal_id: str | None = None
    created_at: str


class Proposal(BaseModel):
    """A paired review: the profile at `base_revision` and its proposed replacement, with replay."""

    id: str
    feedback: str
    model_label: str
    base_revision: int
    current: JobSeekerProfile
    proposed: JobSeekerProfile
    changes: list[ProfileChange]
    replay: ReplayReport
    status: ProposalStatus = "pending"
    applied_revision: int | None = None
    created_at: str = Field(default_factory=_now)
    decided_at: str | None = None

    @computed_field
    @property
    def proposed_digest(self) -> str:
        """SHA-256 of the canonical proposed profile: what an approval must name."""
        return profile_digest(self.proposed)


class Outcome(BaseModel):
    """The result of a workflow run or of a decision on a proposal."""

    proposal_id: str
    status: Literal["no_change", "invalid", "approved", "rejected", "stale"]
    decision: Decision | None = None  # the decision that was recorded first
    base_revision: int | None = None
    applied_revision: int | None = None
    current_revision: int
    duplicate: bool = False  # True when the decision already existed before this call
    route: Literal["interrupt", "commit_retry", "recorded"] | None = None
    message: str


_RECORDED_DECISION: dict[str, Decision] = {"approved": "approve", "rejected": "reject", "stale": "approve"}


class ProfileStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit mode: transactions are explicit (BEGIN IMMEDIATE) and short.
        self._db = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> ProfileStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _write(self) -> Iterator[None]:
        """One write transaction that takes the write lock up front, so what it reads cannot change."""
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    # --- setup and reads ---------------------------------------------------------------------

    def is_initialized(self) -> bool:
        return self._db.execute("SELECT 1 FROM profile_revisions LIMIT 1").fetchone() is not None

    def seed(self, profile: JobSeekerProfile, jobs: Sequence[Job], labels: Sequence[Label]) -> Revision:
        with self._write():
            if self.is_initialized():
                raise AlreadyInitialized(f"{self.path} is already initialized; use a fresh --data-dir")
            self._db.executemany(
                "INSERT INTO jobs (id, data) VALUES (?, ?)", [(j.id, j.model_dump_json()) for j in jobs]
            )
            self._db.executemany(
                "INSERT INTO labels (job_id, verdict, note) VALUES (?, ?, ?)",
                [(label.job_id, label.verdict, label.note) for label in labels],
            )
            self._db.execute(
                "INSERT INTO profile_revisions (revision, profile, proposal_id, created_at) "
                "VALUES (1, ?, NULL, ?)",
                (canonical_json(profile), _now()),
            )
        return self.current()

    @staticmethod
    def _revision(row: sqlite3.Row) -> Revision:
        return Revision(
            revision=row["revision"],
            profile=JobSeekerProfile.model_validate_json(row["profile"]),
            proposal_id=row["proposal_id"],
            created_at=row["created_at"],
        )

    def current(self) -> Revision:
        row = self._db.execute("SELECT * FROM profile_revisions ORDER BY revision DESC LIMIT 1").fetchone()
        if row is None:
            raise NotInitialized(f"{self.path} has no profile yet; run `job-right init` first")
        return self._revision(row)

    def revisions(self) -> list[Revision]:
        return [
            self._revision(r) for r in self._db.execute("SELECT * FROM profile_revisions ORDER BY revision")
        ]

    def jobs(self) -> list[Job]:
        return [
            Job.model_validate_json(r["data"]) for r in self._db.execute("SELECT data FROM jobs ORDER BY id")
        ]

    def labels(self) -> list[Label]:
        rows = self._db.execute("SELECT job_id, verdict, note FROM labels ORDER BY job_id")
        return [Label(job_id=r["job_id"], verdict=r["verdict"], note=r["note"]) for r in rows]

    def packets(self, target_id=None):
        if target_id is None:
            rows = self._db.execute("SELECT data FROM packets ORDER BY rowid DESC")
        else:
            rows = self._db.execute(
                "SELECT data FROM packets WHERE target_id = ? ORDER BY revision DESC", (target_id,)
            )
        return [Packet.model_validate_json(row["data"]) for row in rows]

    def packet(self, packet_id):
        row = self._db.execute("SELECT data FROM packets WHERE id = ?", (packet_id,)).fetchone()
        return Packet.model_validate_json(row["data"]) if row else None

    def save_packet(self, packet):
        with self._write():
            existing = self.packet(packet.id)
            if existing == packet:
                return existing
            latest = self._db.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM packets WHERE target_id = ?", (packet.target.id,)
            ).fetchone()[0]
            if existing or latest + 1 != packet.revision:
                raise CandidateMismatch(
                    "A newer document version was saved. Open the latest version in Documents."
                )
            self._db.execute(
                "INSERT INTO packets VALUES (?, ?, ?, ?)",
                (packet.id, packet.target.id, packet.revision, packet.model_dump_json()),
            )
        return packet

    # --- proposals ---------------------------------------------------------------------------

    @staticmethod
    def _proposal(row: sqlite3.Row) -> Proposal:
        return Proposal(
            id=row["id"],
            feedback=row["feedback"],
            model_label=row["model_label"],
            base_revision=row["base_revision"],
            current=JobSeekerProfile.model_validate_json(row["current"]),
            proposed=JobSeekerProfile.model_validate_json(row["proposed"]),
            changes=[ProfileChange.model_validate(c) for c in json.loads(row["changes"])],
            replay=ReplayReport.model_validate_json(row["replay"]),
            status=row["status"],
            applied_revision=row["applied_revision"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
        )

    def record_proposal(self, proposal: Proposal) -> Proposal:
        """Store a pending proposal once. A retried caller gets the first stored candidate back."""
        with self._write():
            self._db.execute(
                "INSERT OR IGNORE INTO proposals (id, feedback, model_label, base_revision, current, "
                "proposed, changes, replay, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    proposal.id,
                    proposal.feedback,
                    proposal.model_label,
                    proposal.base_revision,
                    canonical_json(proposal.current),
                    canonical_json(proposal.proposed),
                    json.dumps([c.model_dump(mode="json") for c in proposal.changes]),
                    proposal.replay.model_dump_json(),
                    proposal.created_at,
                ),
            )
        return self.proposal(proposal.id)

    def has_proposal(self, proposal_id: str) -> bool:
        return self._db.execute("SELECT 1 FROM proposals WHERE id = ?", (proposal_id,)).fetchone() is not None

    def proposal(self, proposal_id: str) -> Proposal:
        row = self._db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise UnknownProposal(f"no proposal {proposal_id!r}")
        return self._proposal(row)

    def proposals(self) -> list[Proposal]:
        return [self._proposal(r) for r in self._db.execute("SELECT * FROM proposals ORDER BY rowid")]

    # --- the single write path for the profile -----------------------------------------------

    def decide(self, proposal_id: str, decision: Decision, reviewed_digest: str | None = None) -> Outcome:
        """Record the first decision. An approval writes the reviewed candidate as a new revision."""
        if decision not in ("approve", "reject"):
            raise ValueError(f"decision must be 'approve' or 'reject', not {decision!r}")
        if decision == "approve" and not reviewed_digest:
            raise ValueError("approving requires the digest of the reviewed candidate")
        with self._write():
            row = self._db.execute(
                "SELECT status, base_revision, proposed, applied_revision FROM proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise UnknownProposal(f"no proposal {proposal_id!r}")
            current = self._db.execute("SELECT MAX(revision) FROM profile_revisions").fetchone()[0]
            base = row["base_revision"]
            if row["status"] != "pending":
                return self._outcome(proposal_id, row["status"], base, row["applied_revision"], current, True)

            if decision == "reject":
                self._set_status(proposal_id, "rejected")
                return self._outcome(proposal_id, "rejected", base, None, current, False)

            if hashlib.sha256(row["proposed"].encode()).hexdigest() != reviewed_digest:
                raise CandidateMismatch(
                    f"approval for {proposal_id!r} names a different candidate than the stored one"
                )
            if base != current:
                self._set_status(proposal_id, "stale")
                return self._outcome(proposal_id, "stale", base, None, current, False)

            new_revision = current + 1
            self._db.execute(
                "INSERT INTO profile_revisions (revision, profile, proposal_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (new_revision, row["proposed"], proposal_id, _now()),
            )
            self._set_status(proposal_id, "approved", applied_revision=new_revision)
            return self._outcome(proposal_id, "approved", base, new_revision, new_revision, False)

    def _set_status(
        self, proposal_id: str, status: ProposalStatus, applied_revision: int | None = None
    ) -> None:
        self._db.execute(
            "UPDATE proposals SET status = ?, applied_revision = ?, decided_at = ? WHERE id = ?",
            (status, applied_revision, _now(), proposal_id),
        )

    @staticmethod
    def _outcome(
        proposal_id: str,
        status: ProposalStatus,
        base: int,
        applied: int | None,
        current: int,
        duplicate: bool,
    ) -> Outcome:
        if status == "approved":
            message = (
                f"{proposal_id} was already approved (revision {applied}); nothing was applied again."
                if duplicate
                else f"Approved {proposal_id}: profile revision {applied} is now current."
            )
        elif status == "rejected":
            message = (
                f"{proposal_id} was already rejected; nothing changed."
                if duplicate
                else f"{proposal_id} was rejected; the current profile (revision {current}) is unchanged."
            )
        else:
            message = (
                f"{proposal_id} is stale: it was proposed against revision {base}, but revision {current} "
                f"is current. Nothing was applied; run propose again to review it against revision {current}."
            )
        return Outcome(
            proposal_id=proposal_id,
            status=status,
            decision=_RECORDED_DECISION[status],
            base_revision=base,
            applied_revision=applied,
            current_revision=current,
            duplicate=duplicate,
            message=message,
        )
