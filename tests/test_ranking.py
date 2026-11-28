"""Deterministic filters with actionable reasons, employer exceptions and ranking (SPEC example 1)."""

from __future__ import annotations

from conftest import profile_with
from job_right.domain import EmployerException, Job, employer_key
from job_right.ranking import evaluate, recommend
from job_right.synthetic import INITIAL_PROFILE, SYNTHETIC_JOBS, SYNTHETIC_LABELS

JOBS = {job.id: job for job in SYNTHETIC_JOBS}


def test_synthetic_dataset_is_small_and_consistent():
    # SPEC default assumption: synthetic data only, ~14 listings and 8 labels.
    assert len(SYNTHETIC_JOBS) == 14
    assert len({job.id for job in SYNTHETIC_JOBS}) == 14
    assert len(SYNTHETIC_LABELS) == 8
    assert {label.job_id for label in SYNTHETIC_LABELS} <= set(JOBS)
    assert sorted(label.verdict for label in SYNTHETIC_LABELS) == ["rejected"] * 4 + ["wanted"] * 4


def test_employer_key_is_a_stable_slug():
    assert employer_key("Fernhill Freight") == "fernhill-freight"
    assert employer_key("Orbit & Oak Analytics") == "orbit-oak-analytics"


def test_initial_profile_ranking_is_deterministic_with_salary_tie_break():
    shown = recommend(INITIAL_PROFILE, SYNTHETIC_JOBS).shown
    assert [e.job_id for e in shown] == [
        "jr-106",
        "jr-101",  # 38 points, ties jr-110 on score; higher salary_max wins
        "jr-110",
        "jr-102",
        "jr-107",
        "jr-105",
        "jr-113",  # 27 points, ties jr-114; higher salary_max wins
        "jr-114",
        "jr-111",
    ]
    # score = 10 x matched preferred skills + salary_max // 10_000
    assert evaluate(INITIAL_PROFILE, JOBS["jr-101"]).score == 2 * 10 + 18


def test_full_tie_falls_back_to_job_id():
    base = JOBS["jr-110"]
    twin_b = base.model_copy(update={"id": "jr-900b"})
    twin_a = base.model_copy(update={"id": "jr-900a"})
    shown = recommend(INITIAL_PROFILE, [twin_b, twin_a]).shown
    assert [e.job_id for e in shown] == ["jr-900a", "jr-900b"]


def test_each_filter_explains_itself_with_a_concrete_fix():
    hidden = {e.job_id: e for e in recommend(INITIAL_PROFILE, SYNTHETIC_JOBS).hidden}
    assert set(hidden) == {"jr-103", "jr-104", "jr-108", "jr-109", "jr-112"}

    rules = {job_id: {r.rule for r in e.reasons} for job_id, e in hidden.items()}
    assert rules["jr-103"] == {"seniority"}
    assert rules["jr-104"] == {"industry"}
    assert rules["jr-108"] == {"role"}
    assert rules["jr-109"] == {"work_mode", "location"}
    assert rules["jr-112"] == {"salary", "seniority"}

    salary = next(r for r in hidden["jr-112"].reasons if r.rule == "salary")
    assert "$125,000" in salary.detail and "$150,000" in salary.detail
    assert "min_salary" in salary.fix and "$125,000" in salary.fix
    industry = hidden["jr-104"].reasons[0]
    assert "gambling" in industry.detail and "excluded_industries" in industry.fix
    location = next(r for r in hidden["jr-109"].reasons if r.rule == "location")
    assert "Austin" in location.detail and "locations" in location.fix


def test_employer_exception_overrides_global_work_modes():
    remote_only = profile_with(INITIAL_PROFILE, work_modes=["remote"])
    # Cobalt Orchard Health is onsite; the profile's employer exception keeps it visible.
    assert evaluate(remote_only, JOBS["jr-102"]).shown
    # Fernhill Freight has no exception, so its hybrid role is hidden, and the fix names the employer.
    fernhill = evaluate(remote_only, JOBS["jr-101"])
    assert not fernhill.shown
    (reason,) = fernhill.reasons
    assert reason.rule == "work_mode"
    assert "hybrid" in reason.detail
    assert "hybrid" in reason.fix and "Fernhill Freight" in reason.fix and "exception" in reason.fix

    with_exception = profile_with(
        remote_only,
        employer_exceptions={
            **remote_only.model_dump(mode="json")["employer_exceptions"],
            "fernhill-freight": {"work_modes": ["remote", "hybrid"]},
        },
    )
    assert evaluate(with_exception, JOBS["jr-101"]).shown


def test_remote_jobs_ignore_location():
    job = Job(
        id="jr-950",
        title="Backend Engineer",
        employer="Nowhere Particular",
        role="backend",
        work_mode="remote",
        city="Remote",
        seniority="mid",
        industry="logistics",
        salary_min=150_000,
        salary_max=160_000,
        skills=("python",),
    )
    assert evaluate(INITIAL_PROFILE, job).shown


def test_employer_exception_requires_at_least_one_mode():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EmployerException(work_modes=[])
