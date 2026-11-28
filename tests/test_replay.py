"""Replay a proposed profile against earlier wanted/rejected labels (SPEC example 1, agreement assumption)."""

from __future__ import annotations

from conftest import profile_with
from job_right.replay import replay
from job_right.synthetic import INITIAL_PROFILE, SYNTHETIC_JOBS, SYNTHETIC_LABELS


def ids(rows):
    return [row.job_id for row in rows]


def test_remote_only_would_hide_a_previously_wanted_job_with_an_actionable_fix():
    proposed = profile_with(INITIAL_PROFILE, work_modes=["remote"])
    report = replay(INITIAL_PROFILE, proposed, SYNTHETIC_JOBS, SYNTHETIC_LABELS)

    assert ids(report.wanted_newly_hidden) == ["jr-101"]
    row = report.wanted_newly_hidden[0]
    assert row.verdict == "wanted" and row.employer == "Fernhill Freight"
    assert row.before.shown and not row.after.shown
    (reason,) = row.after.reasons
    assert reason.rule == "work_mode"
    assert "Fernhill Freight" in reason.fix

    # The change agrees with two earlier rejections...
    assert ids(report.rejected_newly_hidden) == ["jr-106", "jr-113"]
    assert report.wanted_newly_shown == [] and report.rejected_newly_shown == []
    # ...so aggregate agreement improves even though a wanted job disappears: why replay lists rows.
    assert (report.agreement_before, report.agreement_after, report.total) == (6, 7, 8)
    assert report.has_warnings


def test_employer_exception_keeps_the_wanted_job_and_agreement_reaches_eight():
    proposed = profile_with(
        INITIAL_PROFILE,
        work_modes=["remote"],
        employer_exceptions={
            "cobalt-orchard-health": {"work_modes": ["remote", "hybrid", "onsite"]},
            "fernhill-freight": {"work_modes": ["remote", "hybrid"]},
        },
    )
    report = replay(INITIAL_PROFILE, proposed, SYNTHETIC_JOBS, SYNTHETIC_LABELS)
    assert report.wanted_newly_hidden == []
    assert ids(report.rejected_newly_hidden) == ["jr-106", "jr-113"]
    assert (report.agreement_before, report.agreement_after) == (6, 8)
    assert not report.has_warnings


def test_salary_floor_reports_the_exact_cutoff():
    proposed = profile_with(INITIAL_PROFILE, min_salary=160_000)
    report = replay(INITIAL_PROFILE, proposed, SYNTHETIC_JOBS, SYNTHETIC_LABELS)
    assert ids(report.wanted_newly_hidden) == ["jr-107"]
    (reason,) = report.wanted_newly_hidden[0].after.reasons
    assert reason.rule == "salary"
    assert "$158,000" in reason.detail and "$160,000" in reason.detail
    assert "$158,000" in reason.fix


def test_rejected_job_newly_shown_is_flagged():
    proposed = profile_with(INITIAL_PROFILE, excluded_industries=[])
    report = replay(INITIAL_PROFILE, proposed, SYNTHETIC_JOBS, SYNTHETIC_LABELS)
    assert ids(report.rejected_newly_shown) == ["jr-104"]
    assert report.has_warnings
    assert (report.agreement_before, report.agreement_after) == (6, 5)


def test_identical_profiles_change_nothing():
    report = replay(INITIAL_PROFILE, INITIAL_PROFILE, SYNTHETIC_JOBS, SYNTHETIC_LABELS)
    assert report.wanted_newly_hidden == report.wanted_newly_shown == []
    assert report.rejected_newly_hidden == report.rejected_newly_shown == []
    assert report.agreement_before == report.agreement_after == 6
