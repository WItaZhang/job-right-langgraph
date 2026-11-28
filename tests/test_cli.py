"""CLI behaviour: one-command demo, cross-process review display, privacy defaults, opt-in live model path.

No test talks to a paid provider: the live path and LangSmith endpoints are local HTTP stubs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import run_cli, run_cli_json
from job_right.synthetic import INITIAL_PROFILE


class _Recorder(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, handler):
        super().__init__(("127.0.0.1", 0), handler)
        self.requests: list[dict] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


def _serve(handler) -> Iterator[_Recorder]:
    server = _Recorder(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


class _SinkHandler(BaseHTTPRequestHandler):
    """Accepts anything (stands in for a LangSmith endpoint) and records the request path."""

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.server.requests.append({"method": self.command, "path": self.path})
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = do_PUT = _record

    def log_message(self, *args):
        pass


class _OpenAIStubHandler(BaseHTTPRequestHandler):
    """A minimal OpenAI-compatible chat completions endpoint that answers trustcall's PatchDoc tool."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append({"path": self.path, "body": request})
        arguments = {
            "json_doc_id": "job-seeker-profile",
            "planned_edits": "Raise min_salary to 175000 as requested.",
            "patches": [{"op": "replace", "path": "/min_salary", "value": 175000}],
        }
        body = json.dumps(
            {
                "id": "chatcmpl-stub",
                "object": "chat.completion",
                "created": 0,
                "model": request.get("model", "stub-model"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_stub_1",
                                    "type": "function",
                                    "function": {"name": "PatchDoc", "arguments": json.dumps(arguments)},
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def sink() -> Iterator[_Recorder]:
    yield from _serve(_SinkHandler)


@pytest.fixture
def openai_stub() -> Iterator[_Recorder]:
    yield from _serve(_OpenAIStubHandler)


def test_one_command_demo_runs_every_step_in_fresh_processes(tmp_path):
    # SPEC example 10.
    proc = subprocess.run(
        [sys.executable, "-m", "job_right", "demo", "--data-dir", str(tmp_path / "demo")],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert "fixture" in out and "not an LLM" in out
    assert "$ job-right propose --id remote-only" in out
    assert "Fernhill Freight" in out  # the wanted job that remote-only would hide
    assert "rejected" in out and "stale" in out and "already approved" in out
    assert "revision 3" in out
    assert len(set(line for line in out.splitlines() if line.startswith("[pid "))) >= 8  # separate processes
    assert "Demo finished" in out


def test_show_renders_the_pending_review_from_another_process(tmp_path):
    data_dir = tmp_path / "d"
    run_cli(data_dir, "init")
    run_cli(data_dir, "propose", "--id", "p-show", "Only show me remote roles.")
    out = run_cli(data_dir, "show", "p-show").stdout
    assert "p-show" in out and "pending" in out
    assert "work_modes" in out
    assert "would hide" in out and "Fernhill Freight" in out
    assert "exception" in out  # the actionable fix
    assert "fixture" in out


def test_init_refuses_to_overwrite(tmp_path):
    data_dir = tmp_path / "d"
    assert run_cli_json(data_dir, "init")["revision"] == 1
    again = run_cli(data_dir, "init", check=False)
    assert again.returncode != 0
    assert run_cli_json(data_dir, "profile")["profile"] == INITIAL_PROFILE.model_dump(mode="json")


def test_remote_tracing_is_off_by_default(tmp_path, sink):
    data_dir = tmp_path / "d"
    env = {
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGSMITH_API_KEY": "lsv2_test_not_a_real_key",
        "LANGSMITH_ENDPOINT": sink.url,
        "LANGCHAIN_ENDPOINT": sink.url,
    }
    run_cli(data_dir, "init", env=env)
    run_cli(data_dir, "propose", "--id", "p-private", "Only show me remote roles.", env=env)
    assert sink.requests == []

    # Positive control: the same run with explicit opt-in does reach the (local) endpoint.
    run_cli(
        data_dir,
        "propose",
        "--id",
        "p-optin",
        "Add Rust to my preferred skills.",
        env={**env, "JOB_RIGHT_ALLOW_LANGSMITH": "1"},
    )
    assert sink.requests, "opt-in tracing should have sent something to the local stub endpoint"


def test_live_model_path_via_init_chat_model_against_a_local_openai_compatible_stub(tmp_path, openai_stub):
    data_dir = tmp_path / "d"
    env = {
        "OPENAI_API_KEY": "sk-test-not-a-real-key",
        "OPENAI_BASE_URL": f"{openai_stub.url}/v1",
        "OPENAI_API_BASE": f"{openai_stub.url}/v1",
    }
    run_cli(data_dir, "init")
    result = run_cli_json(
        data_dir, "propose", "--id", "p-live", "--model", "openai:stub-model", "Nothing under $175k.", env=env
    )
    proposal = result["proposal"]
    assert proposal["model_label"].startswith("live")
    assert proposal["proposed"]["min_salary"] == 175_000
    assert proposal["proposed"]["employer_exceptions"] == proposal["current"]["employer_exceptions"]

    (request,) = openai_stub.requests
    assert request["path"].endswith("/chat/completions")
    assert [tool["function"]["name"] for tool in request["body"]["tools"]] == ["PatchDoc"]
    prompt = json.dumps(request["body"]["messages"])
    assert "Nothing under $175k." in prompt and "job-seeker-profile" in prompt
    # Only preferences and feedback go to the model: no job listing text.
    assert "Fernhill Freight" not in prompt and "Lanternfish" not in prompt

    outcome = run_cli_json(data_dir, "approve", "p-live")
    assert (outcome["status"], outcome["applied_revision"]) == ("approved", 2)


def test_unknown_model_spec_is_rejected(tmp_path):
    data_dir = tmp_path / "d"
    run_cli(data_dir, "init")
    proc = run_cli(
        data_dir, "propose", "--model", "not-a-provider-spec", "Add Rust to my preferred skills.", check=False
    )
    assert proc.returncode != 0
    assert "provider:model" in proc.stderr
