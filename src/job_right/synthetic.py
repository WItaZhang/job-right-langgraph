"""Synthetic fixtures written for this project. Every employer, listing, label and preference is fictional."""

from __future__ import annotations

from .domain import EmployerException, Job, JobSeekerProfile, Label


def _job(
    id: str,
    title: str,
    employer: str,
    role: str,
    work_mode: str,
    city: str,
    seniority: str,
    industry: str,
    salary: tuple[int, int],
    skills: str,
) -> Job:
    return Job.model_validate(
        {
            "id": id,
            "title": title,
            "employer": employer,
            "role": role,
            "work_mode": work_mode,
            "city": city,
            "seniority": seniority,
            "industry": industry,
            "salary_min": salary[0],
            "salary_max": salary[1],
            "skills": tuple(skills.split()),
        }
    )


SYNTHETIC_JOBS: tuple[Job, ...] = (
    _job("jr-101", "Senior Backend Engineer", "Fernhill Freight", "backend", "hybrid", "Portland", "senior",
         "logistics", (160_000, 185_000), "python postgres kafka"),
    _job("jr-102", "Platform Engineer", "Cobalt Orchard Health", "platform", "onsite", "Seattle", "mid",
         "healthcare", (150_000, 172_000), "kubernetes terraform python"),
    _job("jr-103", "Staff Backend Engineer", "Pinecone Ledger", "backend", "remote", "Remote", "staff",
         "fintech", (210_000, 240_000), "go postgres"),
    _job("jr-104", "Backend Engineer", "Marigold Gaming", "backend", "remote", "Remote", "mid",
         "gambling", (155_000, 175_000), "python redis"),
    _job("jr-105", "Senior Platform Engineer", "Saltmarsh Robotics", "platform", "remote", "Remote", "senior",
         "robotics", (170_000, 195_000), "kubernetes rust go"),
    _job("jr-106", "Data Platform Engineer", "Copperline Data", "platform", "hybrid", "Seattle", "senior",
         "analytics", (165_000, 190_000), "python spark kubernetes"),
    _job("jr-107", "Backend Engineer, Payments", "Lanternfish Labs", "backend", "remote", "Remote", "mid",
         "fintech", (140_000, 158_000), "python postgres"),
    _job("jr-108", "Frontend Engineer", "Driftwood Media", "frontend", "remote", "Remote", "mid",
         "media", (130_000, 150_000), "typescript react"),
    _job("jr-109", "Senior Backend Engineer", "Kestrel Grid", "backend", "onsite", "Austin", "senior",
         "energy", (175_000, 200_000), "rust postgres"),
    _job("jr-110", "Platform Reliability Engineer", "Orbit & Oak Analytics", "platform", "remote", "Remote",
         "senior", "analytics", (160_000, 180_000), "kubernetes python terraform"),
    _job("jr-111", "Backend Engineer", "Fernhill Freight", "backend", "remote", "Remote", "mid",
         "logistics", (145_000, 165_000), "python kafka"),
    _job("jr-112", "Junior Backend Engineer", "Quillfeather Labs", "backend", "remote", "Remote", "junior",
         "edtech", (110_000, 125_000), "python"),
    _job("jr-113", "Senior Backend Engineer", "Hollowbrook Insurance", "backend", "hybrid", "Portland",
         "senior", "insurance", (155_000, 178_000), "java postgres"),
    _job("jr-114", "Infrastructure Engineer", "Saltmarsh Robotics", "platform", "hybrid", "Seattle", "mid",
         "robotics", (150_000, 170_000), "rust kubernetes"),
)  # fmt: skip

SYNTHETIC_LABELS: tuple[Label, ...] = (
    Label(
        job_id="jr-101", verdict="wanted", note="Saved: strong Postgres team, two office days in Portland."
    ),
    Label(job_id="jr-102", verdict="wanted", note="Saved: would go onsite for this team."),
    Label(job_id="jr-105", verdict="wanted", note="Saved."),
    Label(job_id="jr-107", verdict="wanted", note="Saved: payments work."),
    Label(job_id="jr-104", verdict="rejected", note="Not interested in gambling."),
    Label(job_id="jr-106", verdict="rejected", note="Mostly Spark pipelines."),
    Label(job_id="jr-109", verdict="rejected", note="Onsite in Austin."),
    Label(job_id="jr-113", verdict="rejected", note="Java-heavy stack."),
)

INITIAL_PROFILE = JobSeekerProfile(
    roles=["backend", "platform"],
    work_modes=["remote", "hybrid"],
    locations=["Seattle", "Portland"],
    min_salary=150_000,
    seniority=["mid", "senior"],
    excluded_industries=["gambling"],
    preferred_skills=["python", "postgres", "kubernetes"],
    employer_exceptions={
        "cobalt-orchard-health": EmployerException(work_modes=["remote", "hybrid", "onsite"])
    },
)
