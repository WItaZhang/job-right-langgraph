"""Command-line entry point for the preference workspace."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .chat_models import resolve_chat_model
from .errors import JobRightError
from .graph import Workflow
from .store import Proposal


def _parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", type=Path, default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(
        prog="job-right", description="Review job preferences on synthetic listings.", parents=[common]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "profile", "recommend", "history", "demo"):
        commands.add_parser(name, parents=[common])
    web = commands.add_parser("web", parents=[common], help="Open the local preference workspace")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--model", default="fixture")
    propose = commands.add_parser("propose", parents=[common])
    propose.add_argument("--id")
    propose.add_argument("--model", default="fixture")
    propose.add_argument("feedback")
    for name in ("show", "approve", "reject"):
        commands.add_parser(name, parents=[common]).add_argument("id")
    return parser


def _review(p: Proposal):
    lines = [f"{p.id} · {p.status} · from revision {p.base_revision}", p.model_label, p.feedback]
    for edit in p.changes:
        lines.append(f"  {edit.field}: {edit.before} → {edit.after}")
    r = p.replay
    lines.append(f"Agreement with {r.total} earlier labels: {r.agreement_before} → {r.agreement_after}")
    for title, rows in (
        ("Wanted jobs this change would hide", r.wanted_newly_hidden),
        ("Rejected jobs this change would show", r.rejected_newly_shown),
        ("Wanted jobs this change would show", r.wanted_newly_shown),
        ("Rejected jobs this change would hide", r.rejected_newly_hidden),
    ):
        lines.append(f"{title}: {len(rows)}")
        for row in rows:
            lines.append(f"  {row.job_id} · {row.title} at {row.employer}")
            for reason in (row.before if row.after.shown else row.after).reasons:
                lines.append(f"    {reason.detail}. Possible adjustment: {reason.fix}.")
    return "\n".join(lines)


def _demo(directory):
    steps = [
        ["init"],
        ["recommend"],
        ["propose", "--id", "remote-only", "Only show me remote roles."],
        ["show", "remote-only"],
        ["reject", "remote-only"],
        ["propose", "--id", "salary", "Raise my salary floor to $160k."],
        [
            "propose",
            "--id",
            "keep-fernhill",
            "Only show me remote roles, but keep Fernhill Freight's hybrid roles.",
        ],
        ["approve", "keep-fernhill"],
        ["approve", "keep-fernhill"],
        ["approve", "salary"],
        ["propose", "--id", "salary-again", "Raise my salary floor to $160k."],
        ["approve", "salary-again"],
        ["history"],
    ]
    with TemporaryDirectory() as temp:
        for args in steps:
            print(f"$ job-right {shlex.join(args)}", flush=True)
            with subprocess.Popen(
                [sys.executable, "-m", "job_right", "--data-dir", str(directory or temp), *args]
            ) as child:
                print(f"[pid {child.pid}]", flush=True)
                if child.wait():
                    return 1
    print("Demo finished.")
    return 0


def _run(wf, args):
    cmd = args.command
    if cmd == "init":
        revision = wf.initialize()
        return revision.model_dump(mode="json"), "Synthetic workspace initialized at revision 1."
    if cmd == "profile":
        return wf.store.current().model_dump(mode="json"), None
    if cmd == "recommend":
        ranked = wf.recommendations()
        jobs = {job.id: job for job in wf.store.jobs()}
        lines = [f"{e.score:3}  {jobs[e.job_id].title} · {jobs[e.job_id].employer}" for e in ranked.shown]
        return ranked.model_dump(mode="json"), "\n".join(["Recommended synthetic jobs", *lines])
    if cmd == "propose":
        result = wf.propose(args.feedback, args.id, resolve_chat_model(args.model))
        review = _review(result.proposal) if result.proposal else result.outcome.message
        return result.model_dump(mode="json"), review
    if cmd == "show":
        proposal = wf.store.proposal(args.id)
        return proposal.model_dump(mode="json"), _review(proposal)
    if cmd == "history":
        revisions, proposals = wf.store.revisions(), wf.store.proposals()
        lines = [f"Profile revision {r.revision} · {r.proposal_id or 'initial profile'}" for r in revisions]
        lines.extend(f"{p.status:8} {p.id} · {p.feedback}" for p in proposals)
        return {
            "revisions": [r.model_dump(mode="json") for r in revisions],
            "proposals": [p.model_dump(mode="json") for p in proposals],
        }, "\n".join(lines)
    outcome = wf.decide(args.id, cmd)
    return outcome.model_dump(mode="json"), outcome.message


def main(argv=None):
    args = _parser().parse_args(argv)
    directory = getattr(args, "data_dir", None)
    try:
        if args.command == "demo":
            return _demo(directory)
        if args.command == "web":
            from .web import serve

            serve(directory or Path(".job-right"), args.port, args.model)
            return 0
        with Workflow(directory or Path(".job-right")) as wf:
            data, rendered = _run(wf, args)
        print(
            json.dumps(data, indent=2, ensure_ascii=False)
            if getattr(args, "json", False) or rendered is None
            else rendered
        )
    except (JobRightError, ValueError, ImportError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0
