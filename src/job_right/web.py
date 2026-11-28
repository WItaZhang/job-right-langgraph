"""Loopback-only, server-rendered preference workspace."""

from __future__ import annotations

import secrets
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from flask import Flask, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException, SecurityError

from .chat_models import resolve_chat_model
from .errors import AlreadyInitialized, JobRightError, ProposalIdInUse, UnknownProposal
from .graph import Workflow
from .ranking import recommend

FIELDS = {
    "roles": "Role families",
    "work_modes": "Work arrangement",
    "locations": "Office locations",
    "min_salary": "Salary floor",
    "seniority": "Experience level",
    "excluded_industries": "Excluded industries",
    "preferred_skills": "Preferred skills",
    "employer_exceptions": "Employer exceptions",
}
EXAMPLES = {
    "Remote only": "Only show me remote roles.",
    "Salary floor": "Raise my salary floor to $160k.",
    "Prefer Rust": "Add Rust to my preferred skills.",
    "Keep Fernhill": "Only show me remote roles, but keep Fernhill Freight's hybrid roles.",
}


def field_name(field):
    base, _, company = field.partition(".")
    return FIELDS.get(base, base) + (" · " + company.replace("-", " ").title() if company else "")


def pretty(value, field=""):
    if value is None or value == {} or value == []:
        return "None"
    if field == "min_salary":
        return f"${value:,}"
    if isinstance(value, dict):
        if "work_modes" in value:
            return pretty(value["work_modes"])
        return " · ".join(f"{k.replace('-', ' ').title()} ({pretty(v)})" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(v.capitalize() if v.islower() else v for v in value)
    return str(value)


def _effects(proposal, jobs):
    """Rank changes for the whole catalog, including unrated jobs and skill-only edits."""
    before = recommend(proposal.current, jobs)
    after = recommend(proposal.proposed, jobs)
    old_rank = {row.job_id: i for i, row in enumerate(before.shown, 1)}
    new_rank = {row.job_id: i for i, row in enumerate(after.shown, 1)}
    old = {e.job_id: e for e in before.shown + before.hidden}
    new = {e.job_id: e for e in after.shown + after.hidden}
    effects = []
    for job in jobs:
        a, b = old_rank.get(job.id), new_rank.get(job.id)
        if a == b and (a is None or old[job.id].score == new[job.id].score):
            continue
        effects.append((job, a, b, old[job.id], new[job.id]))
    return effects


def create_app(data_dir: Path, model="fixture"):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        TRUSTED_HOSTS=["localhost", "127.0.0.1"],
        MAX_CONTENT_LENGTH=32_768,
        SESSION_COOKIE_SAMESITE="Strict",
    )
    token = secrets.token_urlsafe(32)
    model_label = resolve_chat_model(model).label
    with Workflow(data_dir) as wf:
        if not wf.store.is_initialized():
            with suppress(AlreadyInitialized):  # Another local process may seed first.
                wf.initialize()

    def workflow():
        if "workflow" not in g:
            g.workflow = Workflow(data_dir)
        return g.workflow

    @app.teardown_appcontext
    def close(exc):
        if wf := g.pop("workflow", None):
            wf.close()

    @app.before_request
    def check_write():
        # Validate Host even on static and error paths (including DNS rebinding).
        host = request.host_url.rstrip("/")
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if (
                origin is not None
                and origin != host
                or request.headers.get("Sec-Fetch-Site") == "cross-site"
                or not secrets.compare_digest(request.form.get("token", "").encode(), token.encode())
            ):
                abort(403, "Reopen this page from the local workspace, then try again.")

    @app.after_request
    def headers(response):
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": (
                    "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                ),
            }
        )
        return response

    app.jinja_env.filters.update(pretty=pretty, field_name=field_name)
    app.jinja_env.globals.update(
        token=token,
        examples=EXAMPLES,
        model_label=model_label,
        fixture=model_label.startswith("fixture"),
        new_id=lambda: uuid4().hex,
    )

    @app.get("/")
    def matches():
        wf = workflow()
        current = wf.store.current()
        jobs = {j.id: j for j in wf.store.jobs()}
        ranked = recommend(current.profile, list(jobs.values()))
        pending = [p for p in reversed(wf.store.proposals()) if p.status == "pending"]
        source = wf.store.proposal(request.args["retry"]) if request.args.get("retry") else None
        return render_template(
            "matches.html",
            current=current,
            jobs=jobs,
            ranked=ranked,
            labels={v.job_id: v for v in wf.store.labels()},
            pending=pending,
            filtered=request.args.get("view") == "filtered",
            feedback=source.feedback if source else request.args.get("example", ""),
        )

    @app.get("/preferences")
    def preferences():
        return render_template("preferences.html", current=workflow().store.current())

    @app.get("/activity")
    def activity():
        store = workflow().store
        return render_template(
            "activity.html",
            proposals=list(reversed(store.proposals())),
            revisions=list(reversed(store.revisions())),
        )

    @app.post("/reviews")
    def propose():
        feedback = request.form.get("feedback", "").strip()
        if not feedback or len(feedback) > 1500:
            abort(400, "Enter between 1 and 1,500 characters of preference feedback.")
        wf = workflow()
        proposal_id = request.form.get("proposal_id", "")
        try:
            result = wf.propose(feedback, proposal_id, resolve_chat_model(model))
        except ProposalIdInUse:
            if wf.store.has_proposal(proposal_id) and wf.store.proposal(proposal_id).feedback == feedback:
                return redirect(url_for("review", proposal_id=proposal_id), 303)
            flash("This form has already been used. Preview your feedback again.", "studio")
        else:
            if result.proposal:
                return redirect(url_for("review", proposal_id=result.proposal.id), 303)
            flash(result.outcome.message, "studio")
        return redirect(url_for("matches", example=feedback, _anchor="studio"), 303)

    @app.get("/reviews/<proposal_id>")
    def review(proposal_id):
        store = workflow().store
        p = store.proposal(proposal_id)
        jobs = store.jobs()
        return render_template(
            "review.html",
            p=p,
            current=store.current(),
            effects=_effects(p, jobs),
            jobs={j.id: j for j in jobs},
            labels={v.job_id: v for v in store.labels()},
        )

    @app.post("/reviews/<proposal_id>/<decision>")
    def decide(proposal_id, decision):
        if decision not in ("approve", "reject"):
            abort(404)
        digest = request.form.get("digest", "")
        if len(digest) != 64:
            abort(400, "Open the review again to confirm its current contents.")
        outcome = workflow().decide(proposal_id, decision, digest)
        if outcome.duplicate:
            flash(f"This review was already {outcome.status}. The first decision is recorded.", "notice")
        return redirect(url_for("review", proposal_id=proposal_id), 303)

    @app.errorhandler(JobRightError)
    def app_error(exc):
        code = 404 if isinstance(exc, UnknownProposal) else 409
        return render_template("error.html", message=str(exc), code=code), code

    @app.errorhandler(HTTPException)
    def http_error(exc):
        if isinstance(exc, SecurityError):
            return exc.get_response()  # Untrusted Host has no URL adapter for rendering links.
        return render_template("error.html", message=exc.description, code=exc.code), exc.code

    @app.errorhandler(500)
    def server_error(exc):
        return render_template(
            "error.html",
            code=500,
            message="Open Activity to check whether the review was "
            "saved. You can return to Matches to retry your feedback.",
        ), 500

    return app


def serve(data_dir, port, model):
    if not 1 <= port <= 65535:
        raise ValueError("Choose a port between 1 and 65535.")
    app = create_app(data_dir, model)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
