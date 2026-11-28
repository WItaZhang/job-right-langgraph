"""Private, version-checked storage for both SQLite databases on stateless Vercel workers."""

from __future__ import annotations

import io
import os
import re
import secrets
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from flask import g, render_template, request, session

from .graph import Workflow
from .store import STORE_FILENAME

DATABASES = (STORE_FILENAME, "checkpoints.sqlite")


class WriteConflict(Exception):
    pass


class PrivateBlob:
    def __init__(self, token):
        match = re.fullmatch(r"vercel_blob_rw_([a-zA-Z0-9]+)_.+", token)
        if not match:
            raise RuntimeError("Configure the project's private Blob connection.")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.origin = f"https://{match[1].lower()}.private.blob.vercel-storage.com"

    def read(self, name):
        response = httpx.get(
            f"{self.origin}/workspaces/{name}.zip",
            params={"cache": "0"},  # Private Blob consistent read, including immediately after a write.
            headers=self.headers,
            timeout=20,
        )
        if response.status_code == 404:
            return None, None
        response.raise_for_status()
        return response.content, response.headers["etag"]

    def write(self, name, body, etag):
        headers = {
            **self.headers,
            "x-api-version": "12",
            "x-vercel-blob-access": "private",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1" if etag else "0",
            "x-cache-control-max-age": "0",
            "x-content-type": "application/zip",
        }
        if etag:
            headers["x-if-match"] = etag
        response = httpx.put(
            "https://blob.vercel-storage.com",
            params={"pathname": f"workspaces/{name}.zip"},
            headers=headers,
            content=body,
            timeout=30,
        )
        if response.status_code in (409, 412) or (
            response.status_code == 400 and "already exists" in response.text.lower()
        ):
            raise WriteConflict
        response.raise_for_status()


def _release():
    if wf := g.pop("workflow", None):
        wf.close()
    if temp := g.pop("scratch", None):
        temp.cleanup()


def open_workspace(blob):
    temp = TemporaryDirectory(prefix="workspace-")
    g.scratch = temp
    root = Path(temp.name)
    body, g.etag = (None, None) if g.get("first_visit") else blob.read(session["workspace"])
    if body is None and session.get("saved"):
        # A transient missing object must never turn a saved workspace back into the seed profile.
        raise RuntimeError("The saved workspace is unavailable.")
    if body is not None:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            for name in DATABASES:
                (root / name).write_bytes(z.read(name))
    g.workflow = Workflow(root)
    if body is None:
        g.workflow.initialize()
    return g.workflow


def bind_hosted(app, blob):
    @app.before_request
    def visitor():
        if request.endpoint != "static" and "workspace" not in session:
            g.first_visit = True
            session["workspace"] = secrets.token_hex(24)
            session["csrf"] = secrets.token_urlsafe(32)
            session.permanent = True

    @app.after_request
    def persist(response):
        if request.method != "POST" or response.status_code >= 400 or "workflow" not in g:
            return response
        for attempt in range(3):
            # Closing both last connections checkpoints their WALs before bundling the pair.
            g.pop("workflow").close()
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
                for name in DATABASES:
                    z.write(Path(g.scratch.name) / name, name)
            try:
                blob.write(session["workspace"], data.getvalue(), g.etag)
                session["saved"] = True
                return response
            except WriteConflict:
                _release()
                g.first_visit = False
                session["saved"] = True
                if attempt < 2:
                    try:
                        response = app.make_response(app.dispatch_request())
                    except Exception as exc:
                        response = app.make_response(app.handle_user_exception(exc))
                    if response.status_code >= 400 or "workflow" not in g:
                        return response
        return app.make_response(
            (
                render_template(
                    "error.html",
                    code=409,
                    message="Another tab saved a change. Reopen the latest version to continue.",
                ),
                409,
            )
        )

    @app.teardown_request
    def cleanup(exc):
        _release()


def production_app():
    from .web import create_app

    return create_app(
        None,
        hosted=PrivateBlob(os.environ["BLOB_READ_WRITE_TOKEN"]),
        secret=os.environ["JOB_RIGHT_SESSION_KEY"],
    )
