"""Deterministic listing filters and ranking. Every hidden job says why, and what would keep it."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .domain import Job, JobSeekerProfile

Rule = Literal["role", "work_mode", "location", "salary", "seniority", "industry"]


class FilterReason(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: Rule
    detail: str
    fix: str


class Evaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    shown: bool
    score: int
    reasons: list[FilterReason] = []


class Recommendations(BaseModel):
    shown: list[Evaluation]
    hidden: list[Evaluation]


def _join(values: Iterable[str]) -> str:
    return ", ".join(values) or "nothing"


def score(profile: JobSeekerProfile, job: Job) -> int:
    """10 points per matched preferred skill, plus one point per $10k at the top of the salary range."""
    matched = set(job.skills) & set(profile.preferred_skills)
    return 10 * len(matched) + job.salary_max // 10_000


def evaluate(profile: JobSeekerProfile, job: Job) -> Evaluation:
    """Apply every hard filter (no short-circuit) so a hidden job lists all of its reasons."""
    reasons: list[FilterReason] = []

    if job.role not in profile.roles:
        reasons.append(
            FilterReason(
                rule="role",
                detail=f"{job.role} role; your target roles are {_join(profile.roles)}",
                fix=f"add '{job.role}' to roles",
            )
        )

    exception = profile.employer_exceptions.get(job.employer_key)
    modes = exception.work_modes if exception else profile.work_modes
    if job.work_mode not in modes:
        if exception:
            detail = f"{job.work_mode} role; at {job.employer} you accept {_join(modes)} (employer exception)"
            fix = f"add '{job.work_mode}' to the {job.employer} employer exception"
        else:
            detail = f"{job.work_mode} role; you accept {_join(modes)}"
            fix = (
                f"allow '{job.work_mode}' in work_modes, or add an employer exception for "
                f"{job.employer} that allows {job.work_mode}"
            )
        reasons.append(FilterReason(rule="work_mode", detail=detail, fix=fix))

    if job.work_mode != "remote" and job.city.casefold() not in {c.casefold() for c in profile.locations}:
        reasons.append(
            FilterReason(
                rule="location",
                detail=f"{job.work_mode} in {job.city}, which is not one of your locations "
                f"({_join(profile.locations)})",
                fix=f"add '{job.city}' to locations",
            )
        )

    if profile.min_salary is not None and job.salary_max < profile.min_salary:
        reasons.append(
            FilterReason(
                rule="salary",
                detail=f"pays up to ${job.salary_max:,}, below your ${profile.min_salary:,} floor",
                fix=f"lower min_salary to ${job.salary_max:,} or less",
            )
        )

    if job.seniority not in profile.seniority:
        reasons.append(
            FilterReason(
                rule="seniority",
                detail=f"{job.seniority}-level role; you target {_join(profile.seniority)}",
                fix=f"add '{job.seniority}' to seniority",
            )
        )

    if job.industry in profile.excluded_industries:
        reasons.append(
            FilterReason(
                rule="industry",
                detail=f"industry '{job.industry}' is excluded",
                fix=f"remove '{job.industry}' from excluded_industries",
            )
        )

    return Evaluation(job_id=job.id, shown=not reasons, score=score(profile, job), reasons=reasons)


def recommend(profile: JobSeekerProfile, jobs: Sequence[Job]) -> Recommendations:
    """Shown jobs ranked by score, then salary_max, then id; hidden jobs ordered by id."""
    salary = {job.id: job.salary_max for job in jobs}
    evaluations = [evaluate(profile, job) for job in jobs]
    shown = sorted((e for e in evaluations if e.shown), key=lambda e: (-e.score, -salary[e.job_id], e.job_id))
    hidden = sorted((e for e in evaluations if not e.shown), key=lambda e: e.job_id)
    return Recommendations(shown=shown, hidden=hidden)
