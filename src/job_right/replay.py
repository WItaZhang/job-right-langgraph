"""Replay: evaluate every explicitly labelled job under the current and the proposed profile."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from .domain import Job, JobSeekerProfile, Label, Verdict
from .ranking import Evaluation, evaluate


class ReplayRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    title: str
    employer: str
    verdict: Verdict
    before: Evaluation
    after: Evaluation


class ReplayReport(BaseModel):
    """Visibility flips among labelled jobs. Agreement = wanted job shown, or rejected job hidden."""

    total: int
    agreement_before: int
    agreement_after: int
    wanted_newly_hidden: list[ReplayRow] = []
    wanted_newly_shown: list[ReplayRow] = []
    rejected_newly_hidden: list[ReplayRow] = []
    rejected_newly_shown: list[ReplayRow] = []

    @property
    def has_warnings(self) -> bool:
        """The change would hide something the user wanted, or show something they rejected."""
        return bool(self.wanted_newly_hidden or self.rejected_newly_shown)


def _agrees(verdict: Verdict, shown: bool) -> bool:
    return shown if verdict == "wanted" else not shown


def replay(
    current: JobSeekerProfile, proposed: JobSeekerProfile, jobs: Sequence[Job], labels: Sequence[Label]
) -> ReplayReport:
    by_id = {job.id: job for job in jobs}
    rows = [
        ReplayRow(
            job_id=label.job_id,
            title=by_id[label.job_id].title,
            employer=by_id[label.job_id].employer,
            verdict=label.verdict,
            before=evaluate(current, by_id[label.job_id]),
            after=evaluate(proposed, by_id[label.job_id]),
        )
        for label in sorted(labels, key=lambda label: label.job_id)
    ]

    def flipped(verdict: Verdict, now_shown: bool) -> list[ReplayRow]:
        return [
            r
            for r in rows
            if r.verdict == verdict and r.before.shown != now_shown and r.after.shown == now_shown
        ]

    return ReplayReport(
        total=len(rows),
        agreement_before=sum(_agrees(r.verdict, r.before.shown) for r in rows),
        agreement_after=sum(_agrees(r.verdict, r.after.shown) for r in rows),
        wanted_newly_hidden=flipped("wanted", False),
        wanted_newly_shown=flipped("wanted", True),
        rejected_newly_hidden=flipped("rejected", False),
        rejected_newly_shown=flipped("rejected", True),
    )
