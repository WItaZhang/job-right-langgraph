"""Typed domain: synthetic job listings, explicit labels and the job seeker's preference profile."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

WorkMode = Literal["remote", "hybrid", "onsite"]
Seniority = Literal["junior", "mid", "senior", "staff"]
Verdict = Literal["wanted", "rejected"]

WORK_MODES: tuple[WorkMode, ...] = ("remote", "hybrid", "onsite")
SENIORITY_LEVELS: tuple[Seniority, ...] = ("junior", "mid", "senior", "staff")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_T = TypeVar("_T", bound=str)


def employer_key(name: str) -> str:
    """Stable slug that keys employer exceptions: 'Orbit & Oak Analytics' -> 'orbit-oak-analytics'."""
    return "-".join(re.findall(r"[a-z0-9]+", name.lower()))


def _ordered(values: list[_T], order: tuple[_T, ...], what: str) -> list[_T]:
    if len(set(values)) != len(values):
        raise ValueError(f"{what} must be distinct")
    return [value for value in order if value in values]


def _terms(values: list[str]) -> list[str]:
    """Lower-case, trim and de-duplicate free-text terms, keeping their order."""
    cleaned: list[str] = []
    for value in values:
        term = value.strip().lower()
        if term and term not in cleaned:
            cleaned.append(term)
    return cleaned


class Job(BaseModel):
    """A fictional job listing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    employer: str
    role: str
    work_mode: WorkMode
    city: str
    seniority: Seniority
    industry: str
    salary_min: int = Field(ge=0)
    salary_max: int = Field(ge=0)
    skills: tuple[str, ...] = ()

    @field_validator("skills")
    @classmethod
    def _lower_skills(cls, skills: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_terms(list(skills)))

    @property
    def employer_key(self) -> str:
        return employer_key(self.employer)


class Label(BaseModel):
    """An explicit earlier judgement on a listing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    verdict: Verdict
    note: str = ""


class EmployerException(BaseModel):
    """Employer-scoped override: its work modes replace the global list for that employer only."""

    model_config = ConfigDict(extra="forbid")

    work_modes: list[WorkMode] = Field(
        min_length=1,
        description="Work modes accepted at this employer only. Replaces the global work_modes for its jobs.",
    )

    @field_validator("work_modes")
    @classmethod
    def _canonical_modes(cls, modes: list[WorkMode]) -> list[WorkMode]:
        return _ordered(modes, WORK_MODES, "work modes")


class JobSeekerProfile(BaseModel):
    """The job seeker's current preferences: one authoritative profile, no history inside it."""

    model_config = ConfigDict(extra="forbid")

    roles: list[str] = Field(
        min_length=1,
        description="Role families to show, e.g. backend or platform. Other role families are hidden.",
    )
    work_modes: list[WorkMode] = Field(
        min_length=1,
        description="Work modes accepted at every employer that has no employer exception.",
    )
    locations: list[str] = Field(
        default_factory=list,
        description="Cities acceptable for hybrid or onsite work. Remote jobs ignore location.",
    )
    min_salary: int | None = Field(
        default=None,
        ge=0,
        le=1_000_000,
        description="Salary floor in USD per year, compared with the top of a job's range. null = no floor.",
    )
    seniority: list[Seniority] = Field(min_length=1, description="Seniority levels to show.")
    excluded_industries: list[str] = Field(
        default_factory=list, description="Industries whose jobs are always hidden."
    )
    preferred_skills: list[str] = Field(
        default_factory=list, description="Skills that rank a job higher. They never hide a job."
    )
    employer_exceptions: dict[str, EmployerException] = Field(
        default_factory=dict,
        description=(
            "Overrides keyed by employer slug (lower-case words joined by '-'). Keep every exception "
            "unless the feedback explicitly withdraws it."
        ),
    )

    @field_validator("roles")
    @classmethod
    def _roles(cls, roles: list[str]) -> list[str]:
        cleaned = _terms(roles)
        if not cleaned:
            raise ValueError("at least one role is required")
        return cleaned

    @field_validator("excluded_industries", "preferred_skills")
    @classmethod
    def _clean_terms(cls, values: list[str]) -> list[str]:
        return _terms(values)

    @field_validator("work_modes")
    @classmethod
    def _work_modes(cls, modes: list[WorkMode]) -> list[WorkMode]:
        return _ordered(modes, WORK_MODES, "work modes")

    @field_validator("seniority")
    @classmethod
    def _seniority(cls, levels: list[Seniority]) -> list[Seniority]:
        return _ordered(levels, SENIORITY_LEVELS, "seniority levels")

    @field_validator("locations")
    @classmethod
    def _locations(cls, cities: list[str]) -> list[str]:
        cleaned: list[str] = []
        for city in (c.strip() for c in cities):
            if city and city.casefold() not in {c.casefold() for c in cleaned}:
                cleaned.append(city)
        return cleaned

    @field_validator("employer_exceptions")
    @classmethod
    def _slug_keys(cls, exceptions: dict[str, EmployerException]) -> dict[str, EmployerException]:
        bad = [key for key in exceptions if not _SLUG.match(key)]
        if bad:
            raise ValueError(f"employer exception keys must be slugs like 'fernhill-freight', got {bad}")
        return dict(sorted(exceptions.items()))


class ProfileChange(BaseModel):
    """One field-level difference between the current and the proposed profile: the typed edit."""

    model_config = ConfigDict(frozen=True)

    field: str
    before: Any = None
    after: Any = None


def diff_profiles(before: JobSeekerProfile, after: JobSeekerProfile) -> list[ProfileChange]:
    """Field-level changes, with one entry per employer exception so each is reviewed on its own."""
    old, new = before.model_dump(mode="json"), after.model_dump(mode="json")
    changes: list[ProfileChange] = []
    for name in JobSeekerProfile.model_fields:
        if name == "employer_exceptions":
            for key in sorted(set(old[name]) | set(new[name])):
                if old[name].get(key) != new[name].get(key):
                    changes.append(
                        ProfileChange(
                            field=f"{name}.{key}", before=old[name].get(key), after=new[name].get(key)
                        )
                    )
        elif old[name] != new[name]:
            changes.append(ProfileChange(field=name, before=old[name], after=new[name]))
    return changes


def canonical_json(profile: JobSeekerProfile) -> str:
    """The exact text stored for a profile; its SHA-256 is the digest a reviewer approves."""
    return json.dumps(
        profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def profile_digest(profile: JobSeekerProfile) -> str:
    return hashlib.sha256(canonical_json(profile).encode()).hexdigest()
