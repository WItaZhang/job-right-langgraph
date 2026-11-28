"""Application document workbench and text-based interchange with Resume and Interview."""

from __future__ import annotations

import io
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from flask import abort, flash, redirect, render_template, request, send_file, url_for

from .materials import (
    SAMPLE_RESUME,
    Target,
    build_packet,
    clean_text,
    document_lines,
    mentions,
    target_skills,
)


def _upload(field, fallback, converter=None):
    upload = request.files.get(field)
    if not upload or not upload.filename:
        return fallback
    data = upload.read(80_001)
    if len(data) > 80_000:
        raise ValueError("Choose a text source smaller than 80 KB.")
    suffix = Path(upload.filename).suffix.lower()
    if suffix == ".tex" and converter:
        with TemporaryDirectory() as directory:
            file = Path(directory) / "resume.tex"
            file.write_bytes(data)
            # The operator chooses the trusted converter at startup. Only the selected file is read.
            try:
                result = subprocess.run(
                    [sys.executable, "-IB", str(converter), str(file)],
                    cwd=directory,
                    timeout=5,
                    capture_output=True,
                    check=True,
                    env={},
                )
            except (subprocess.SubprocessError, OSError) as exc:
                raise ValueError("Could not read this TeX source. Paste the text export instead.") from exc
            data = result.stdout
    elif suffix not in {".txt", ".md"}:
        raise ValueError("Choose a UTF-8 TXT or Markdown file, or paste its text.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Save the file as UTF-8 text and try again.") from exc


def _brief(target):
    return (
        f"# {target.title} — {target.employer}\n\n{target.description}\n\n"
        f"Focus terms: {', '.join(target.skills) or 'See job description'}.\n"
    )


def _download(data, filename, mimetype):
    return send_file(io.BytesIO(data), mimetype=mimetype, as_attachment=True, download_name=filename)


def register_documents(app, workflow, converter=None, writer=None):
    app.jinja_env.globals["writer_model"] = writer

    def packet_or_404(packet_id):
        packet = workflow().store.packet(packet_id)
        if not packet:
            abort(404, "This document version is unavailable in your workspace.")
        return packet

    def get_target(target_id):
        store = workflow().store
        for job in store.jobs():
            if job.id == target_id:
                return Target.from_job(job)
        previous = store.packets(target_id)
        if previous:
            return previous[0].target
        abort(404, "This role is unavailable in your workspace.")

    @app.get("/documents")
    def documents():
        latest = {}
        for packet in workflow().store.packets():
            latest.setdefault(packet.target.id, packet)
        return render_template("documents.html", packets=latest.values())

    @app.get("/documents/new")
    def prepare():
        target_id = request.args.get("job", "")
        target = get_target(target_id) if target_id else None
        history = workflow().store.packets(target_id) if target else []
        if history and "new" not in request.args:
            return redirect(url_for("packet_view", packet_id=history[0].id))
        previous = history or workflow().store.packets()
        return render_template(
            "prepare.html",
            target=target,
            prior=previous[0] if previous else None,
            revision=history[0].revision if history else 0,
            converter=bool(converter),
            sample=SAMPLE_RESUME,
        )

    @app.post("/documents/new")
    def create_packet():
        form = request.form
        try:
            packet_id = form.get("id", "")
            if not re.fullmatch("[0-9a-f]{32}", packet_id):
                abort(400)
            if form.get("target_id"):
                target = get_target(form["target_id"])
            else:
                description = clean_text(form.get("description", ""), "a job description", 12_000)
                target = Target(
                    id="custom-" + packet_id,
                    title=clean_text(form.get("title", ""), "a role title", 150),
                    employer=clean_text(form.get("employer", ""), "an employer", 150),
                    description=description,
                    skills=target_skills(description, form.get("terms", "")),
                )
            base = _upload("resume_file", form.get("base", ""), converter)
            letter = _upload("letter_file", None)
            packet = build_packet(
                target,
                base,
                packet_id,
                int(form.get("revision", "0")) + 1,
                supplied_letter=letter,
                motivation=form.get("motivation", ""),
            )
            if form.get("use_writer") and writer and not letter:
                from .writer import refine

                packet = refine(packet, writer)
            workflow().store.save_packet(packet)
        except ValueError as exc:
            return render_template(
                "prepare.html",
                target=get_target(form["target_id"]) if form.get("target_id") else None,
                prior=None,
                revision=form.get("revision", 0),
                converter=bool(converter),
                sample=SAMPLE_RESUME,
                draft=form,
                problem=str(exc),
            ), 422
        return redirect(url_for("packet_view", packet_id=packet.id), 303)

    @app.get("/documents/<packet_id>")
    def packet_view(packet_id):
        p = packet_or_404(packet_id)
        return render_template(
            "packet.html",
            p=p,
            history=workflow().store.packets(p.target.id),
            letter=request.args.get("tab") == "letter",
            rows=document_lines(p.resume),
            gaps=[s for s in p.target.skills if not mentions(p.resume, s)],
        )

    @app.post("/documents/<packet_id>/edit/<document>")
    def packet_edit(packet_id, document):
        p = packet_or_404(packet_id)
        if document not in ("resume", "letter"):
            abort(404)
        submission = request.form.get("id", "")
        if not re.fullmatch("[0-9a-f]{32}", submission):
            abort(400)
        value = request.form.get("text", "")
        try:
            if document == "resume":
                updated = build_packet(
                    p.target, p.base, submission, p.revision + 1, edited_resume=value, motivation=p.motivation
                )
            else:
                updated = build_packet(
                    p.target,
                    p.base,
                    submission,
                    p.revision + 1,
                    edited_resume=p.resume,
                    supplied_letter=value,
                    motivation=p.motivation,
                )
            workflow().store.save_packet(updated)
        except ValueError as exc:
            return render_template(
                "packet.html",
                p=p,
                history=workflow().store.packets(p.target.id),
                letter=document == "letter",
                rows=document_lines(p.resume),
                gaps=[s for s in p.target.skills if not mentions(p.resume, s)],
                problem=str(exc),
                edit_text=value,
            ), 422
        flash(
            "New version saved." if document == "letter" else "Résumé saved and cover letter rebuilt.",
            "notice",
        )
        return redirect(url_for("packet_view", packet_id=updated.id, tab=document), 303)

    @app.get("/documents/<packet_id>/export/<kind>")
    def packet_export(packet_id, kind):
        p = packet_or_404(packet_id)
        if kind in ("resume", "letter"):
            return _download(getattr(p, kind).encode(), f"{kind}.txt", "text/plain; charset=utf-8")
        if kind != "bundle":
            abort(404)
        claims = "\n".join(f"- Résumé line {c.line}: {c.quote}" for c in p.citations)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("jd.md", _brief(p.target))
            z.writestr("resume.txt", p.resume)
            z.writestr("cover_letter/letter.txt", p.letter)
            z.writestr(
                "cover_letter/claims.md", claims or "Author-edited letter. Verify claims against resume.txt."
            )
        return _download(buf.getvalue(), f"application-v{p.revision}.zip", "application/zip")

    @app.get("/documents/<packet_id>/print/<kind>")
    def packet_print(packet_id, kind):
        p = packet_or_404(packet_id)
        if kind not in ("resume", "letter"):
            abort(404)
        return render_template("print.html", p=p, letter=kind == "letter", rows=document_lines(p.resume))

    @app.get("/roles/<target_id>/brief")
    def job_brief(target_id):
        return _download(_brief(get_target(target_id)).encode(), "jd.md", "text/markdown")
