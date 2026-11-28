import io
import re
import zipfile
from uuid import uuid4

from job_right.materials import SAMPLE_RESUME, Target, build_packet
from job_right.synthetic import SYNTHETIC_JOBS
from job_right.web import create_app


def test_reorders_within_roles_and_letters_cite_the_exact_final_resume():
    job = Target.from_job(SYNTHETIC_JOBS[1])
    pair = build_packet(job, SAMPLE_RESUME, "one", 1)
    assert pair.resume.index("- Wrote Terraform") < pair.resume.index("- Built a Python")
    assert sorted(pair.base.splitlines()) == sorted(pair.resume.splitlines())
    for citation in pair.citations:
        assert pair.resume.splitlines()[citation.line - 1].endswith(citation.quote)
    assert "Cobalt Orchard Health" in pair.letter and "Terraform" in pair.letter
    assert "2022" not in pair.letter


def test_packet_versions_import_export_and_independent_preference_revisions(tmp_path):
    app = create_app(tmp_path)
    client = app.test_client()
    page = client.get("/documents/new?job=jr-102").text
    token = re.search(r'name="token" value="([^"]+)', page)[1]
    data = dict(id=uuid4().hex, token=token, target_id="jr-102", revision="0", base=SAMPLE_RESUME)
    url = client.post("/documents/new", data=data).location
    assert client.post("/documents/new", data=data).location == url
    assert client.post("/documents/new", data={**data, "id": uuid4().hex}).status_code == 409
    result = client.get(url + "/export/bundle")
    with zipfile.ZipFile(io.BytesIO(result.data)) as z:
        assert "Kubernetes" in z.read("cover_letter/claims.md").decode()
        assert "Cobalt Orchard Health" in z.read("jd.md").decode()
    letter = "Dear hiring team,\n\nHere is my reviewed text."
    edit = dict(id=uuid4().hex, token=token, text=letter)
    new = client.post(url + "/edit/letter", data=edit).location.partition("?")[0]
    assert client.get(new + "/export/letter").data.decode() == letter
    assert client.get(url + "/export/letter").data.decode() != letter
    assert "Profile v1" in client.get("/preferences").text
    assert client.post(url + "/edit/letter", data={**edit, "id": uuid4().hex}).status_code == 409


def test_opt_in_writer_uses_valid_evidence_and_rejects_uncited_numbers(monkeypatch):
    from types import SimpleNamespace

    import pytest

    from job_right.writer import FactCheck, Paragraph, WritingPlan, refine

    pair = build_packet(Target.from_job(SYNTHETIC_JOBS[1]), SAMPLE_RESUME, "one", 1)
    fact = pair.citations[0]
    plan = WritingPlan(
        priority_lines=[fact.line],
        paragraphs=[
            Paragraph(text="I enjoy work on developer environments.", source_lines=[]),
            Paragraph(
                text="I wrote Terraform modules for Kubernetes test environments.", source_lines=[fact.line]
            ),
        ],
    )

    class Writer:
        def with_structured_output(self, schema):
            return SimpleNamespace(
                invoke=lambda _: plan if schema is WritingPlan else FactCheck(supported=True, issues=[])
            )

    monkeypatch.setattr("job_right.writer.resolve_chat_model", lambda _: SimpleNamespace(model=Writer()))
    checked = refine(pair, "local:writer")
    assert checked.writer == "local:writer"
    assert checked.resume.splitlines()[checked.citations[0].line - 1].endswith(fact.quote)
    plan.paragraphs[1].text += " I saved 97 hours."
    with pytest.raises(ValueError, match="verified draft"):
        refine(pair, "local:writer")
