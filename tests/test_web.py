"""Check browser-specific write guards and round trips between Web and CLI storage."""

from __future__ import annotations

import re

import pytest

from job_right.graph import Workflow
from job_right.web import create_app


@pytest.fixture
def client(data_dir):
    return create_app(data_dir).test_client()


def field(page, name):
    return re.search(rf'name="{name}" value="([^"]+)"', page.get_data(as_text=True))[1]


def form(client):
    home = client.get("/")
    return {
        "token": field(home, "token"),
        "proposal_id": field(home, "proposal_id"),
        "feedback": "Only show me remote roles.",
    }


def test_only_local_forms_can_write(client, data_dir):
    data = form(client)
    assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 400
    assert client.post("/reviews", data=data, headers={"Origin": "https://example.org"}).status_code == 403
    assert client.post("/reviews", data=data, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/reviews", data={**data, "token": ""}).status_code == 403
    with Workflow(data_dir) as wf:
        assert wf.store.proposals() == []


def test_repeated_preview_and_decision_forms_preserve_the_reviewed_candidate(client, data_dir):
    data = form(client)
    url = client.post("/reviews", data=data).location
    assert client.post("/reviews", data=data).location == url
    digest = field(client.get(url), "digest")
    decision = {"token": data["token"], "digest": digest}
    assert client.post(url + "/approve", data={**decision, "digest": "0" * 64}).status_code == 409
    with Workflow(data_dir) as wf:
        pending = wf.pending(data["proposal_id"])
        assert pending.proposed_digest == digest and wf.store.current().revision == 1
    assert client.post(url + "/approve", data=decision).status_code == 303
    again = client.post(url + "/reject", data=decision, follow_redirects=True)
    assert "already approved" in again.get_data(as_text=True)
    with Workflow(data_dir) as wf:
        assert wf.store.current().profile == pending.proposed
        assert len(wf.store.revisions()) == 2
        assert len(wf.store.proposals()) == 1


def test_no_change_restores_input_and_leaves_no_pending_item(client, data_dir):
    data = {**form(client), "feedback": "Hybrid roles are fine again."}
    html = client.post("/reviews", data=data, follow_redirects=True).get_data(as_text=True)
    assert "No preference changed" in html
    assert data["feedback"] in html
    with Workflow(data_dir) as wf:
        assert wf.store.proposals() == []


def test_keeping_one_employers_hybrid_roles_preserves_its_existing_onsite_exception(client, data_dir):
    data = {
        **form(client),
        "feedback": "Only show me remote roles, but keep Cobalt Orchard Health's hybrid roles.",
    }
    assert client.post("/reviews", data=data).status_code == 303
    with Workflow(data_dir) as wf:
        review = wf.pending(data["proposal_id"])
        assert review.proposed.work_modes == ["remote"]
        assert review.proposed.employer_exceptions == review.current.employer_exceptions
