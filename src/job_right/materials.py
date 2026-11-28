"""Build a job-specific document pair from the applicant's own source text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import groupby

from pydantic import BaseModel, Field

from .domain import Job

SAMPLE_RESUME = """Avery Lane
Software engineer · avery.lane@example.org

## Experience
Software Engineer | North Canal Tools | 2022–present
- Built a Python reconciliation service that flags mismatches before the daily billing close.
- Cut a Postgres reporting query from 42 seconds to 6 seconds by changing its index and query plan.
- Wrote Terraform modules for Kubernetes test environments so engineers could reproduce release failures.
- Added Kafka consumer lag alerts and a replay command for delayed billing events.

## Projects
Release notebook | Open-source project
- Built a Go command-line tool that compares deployment manifests and highlights configuration drift.
- Documented a rollback rehearsal that new contributors can run locally.

## Education
BSc in Computer Science | Elm Ferry College

## Skills
Python, Postgres, Terraform, Kubernetes, Kafka, Go"""

ALIASES = {
    "postgres": ("postgres", "postgresql"),
    "kubernetes": ("kubernetes", "k8s"),
    "javascript": ("javascript", "node.js"),
    "go": ("golang", "Go"),
}
TERMS = (
    "python",
    "postgres",
    "kubernetes",
    "terraform",
    "kafka",
    "go",
    "rust",
    "sql",
    "typescript",
    "javascript",
    "react",
    "redis",
    "spark",
    "java",
    "docker",
    "aws",
    "azure",
    "figma",
    "excel",
    "analytics",
    "research",
    "design",
    "marketing",
    "sales",
    "accessibility",
    "testing",
    "finance",
    "operations",
    "communication",
    "writing",
    "customer support",
    "project management",
)
BULLET = re.compile(r"^\s*[-*•]\s+")
ACTIONS = re.compile(
    r"^(added|analysed|analyzed|authored|automated|built|collaborated|contributed|created|cut|"
    r"delivered|designed|developed|documented|implemented|improved|introduced|launched|led|"
    r"maintained|managed|migrated|operated|partnered|planned|published|reduced|researched|"
    r"shipped|supported|tested|trained|worked|wrote)\b",
    re.I,
)


class Target(BaseModel):
    id: str
    title: str
    employer: str
    description: str
    skills: list[str]
    synthetic: bool = False

    @classmethod
    def from_job(cls, job: Job):
        description = (
            f"{job.seniority.capitalize()} {job.role} role in {job.industry}. "
            f"Work arrangement: {job.work_mode}. Listed skills: {', '.join(job.skills)}."
        )
        return cls(
            id=job.id,
            title=job.title,
            employer=job.employer,
            description=description,
            skills=list(job.skills),
            synthetic=True,
        )


class Citation(BaseModel):
    line: int
    quote: str
    skills: list[str]


class Packet(BaseModel):
    id: str
    revision: int
    target: Target
    base: str
    resume: str
    letter: str
    citations: list[Citation] = Field(default_factory=list)
    writer: str = ""
    manual_letter: bool = False
    motivation: str = ""


def mentions(text: str, term: str) -> bool:
    for alias in ALIASES.get(term, (term,)):
        flags = 0 if alias == "Go" else re.I
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text, flags):
            return True
    return False


def target_skills(description, extra):
    keys = [s.strip().lower() for s in extra.split(",") if s.strip()]
    return list(dict.fromkeys([*keys, *(s for s in TERMS if mentions(description, s))]))[:30]


def clean_text(value, label, limit):
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value or len(value) > limit or "\x00" in value:
        raise ValueError(f"Enter {label} using between 1 and {limit:,} characters.")
    return value


def _matches(text, target):
    return [s for s in target.skills if mentions(text, s)]


def _ordered_bullets(lines, target, priority=None):
    # Wrapped bullet continuations stay attached. Headers, employer boundaries and dates stay in place.
    blocks = []
    for line in lines:
        if line[:1].isspace() and line.strip() and blocks and BULLET.match(blocks[-1]):
            blocks[-1] += "\n" + line
        else:
            blocks.append(line)
    ordered = []
    for is_bullet, chunk in groupby(blocks, key=lambda line: bool(BULLET.match(line))):
        rows = list(chunk)
        if is_bullet:
            if priority:
                rows.sort(
                    key=lambda line: priority.get(" ".join(BULLET.sub("", line).split()), len(priority))
                )
            else:
                rows.sort(key=lambda line: -len(_matches(line, target)))
        ordered.extend(rows)
    return "\n".join(ordered)


def _facts(resume, target):
    lines = resume.splitlines()
    citations = []
    for index, line in enumerate(lines):
        if not (BULLET.match(line) or ACTIONS.match(line.strip()) or line.strip().startswith(("I ", "We "))):
            continue
        quote = BULLET.sub("", line).strip()
        for continuation in lines[index + 1 :]:
            if not continuation[:1].isspace() or not continuation.strip() or BULLET.match(continuation):
                break
            quote += " " + continuation.strip()
        if len(quote.split()) >= 5:
            citations.append(Citation(line=index + 1, quote=quote, skills=_matches(quote, target)))
    return sorted(citations, key=lambda c: -len(c.skills))


def _as_sentence(quote):
    if ACTIONS.match(quote):
        quote = "I " + quote[0].lower() + quote[1:]
    return quote.rstrip(".!?") + "."


def _series(items):
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _display(skill):
    return {
        "go": "Go",
        "postgres": "Postgres",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "sql": "SQL",
        "aws": "AWS",
    }.get(skill, skill.capitalize())


def build_packet(
    target, base, packet_id, revision, *, edited_resume=None, supplied_letter=None, motivation=""
):
    motivation = clean_text(motivation, "a short note", 900) if motivation.strip() else ""
    base = clean_text(base, "a base résumé", 24_000)
    resume = (
        clean_text(edited_resume, "résumé text", 24_000)
        if edited_resume is not None
        else _ordered_bullets(base.splitlines(), target)
    )
    if len(resume.splitlines()) < 2:
        raise ValueError("Start with your name, then add your experience on the following lines.")
    facts = _facts(resume, target)
    if supplied_letter is not None:
        letter = clean_text(supplied_letter, "cover letter text", 12_000)
        return Packet(
            id=packet_id,
            revision=revision,
            target=target,
            base=base,
            resume=resume,
            letter=letter,
            manual_letter=True,
            motivation=motivation,
        )
    if not facts:
        raise ValueError(
            "Add at least one experience bullet, for example “- Built a Python tool for "
            "billing reconciliation.” Include only work you actually did."
        )
    # Avoid repeating two examples of the same skill when another relevant fact is available.
    chosen = [facts[0]]
    seen = set(chosen[0].skills)
    remaining = [f for f in facts[1:] if f.quote != facts[0].quote]
    if remaining:
        chosen.append(max(remaining, key=lambda c: (len(set(c.skills) - seen), len(c.skills))))
    skills = list(dict.fromkeys(_display(s) for c in chosen for s in c.skills))
    intro = f"I’m writing to apply for the {target.title} position at {target.employer}."
    if motivation:
        intro += " " + motivation
    elif skills:
        intro += f" The work you describe with {_series(skills[:3])} caught my attention."
    sign = resume.splitlines()[0].lstrip("# ").strip()
    evidence = " ".join(_as_sentence(c.quote) for c in chosen)
    close = (
        f"I’d welcome the chance to walk your team through "
        f"{'these projects' if len(chosen) > 1 else 'this work'}, including the decisions behind "
        "the implementation and what I would change on a second pass. "
        "Thank you for considering my application."
    )
    letter = (
        f"Dear {target.employer} hiring team,\n\n{intro}\n\n{evidence}\n\n{close}\n\nBest regards,\n{sign}"
    )
    return Packet(
        id=packet_id,
        revision=revision,
        target=target,
        base=base,
        resume=resume,
        letter=letter,
        citations=chosen,
        motivation=motivation,
    )


@dataclass(frozen=True)
class Section:
    kind: str
    text: str


def document_lines(text):
    rows = []
    for index, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        if index == 0:
            kind = "name"
        elif line.startswith("#") or line.casefold() in {
            "experience",
            "skills",
            "education",
            "projects",
            "summary",
        }:
            kind = "heading"
        elif BULLET.match(line):
            kind = "bullet"
        else:
            kind = "body"
        rows.append(Section(kind, BULLET.sub("", line).lstrip("# ").strip()))
    return rows
