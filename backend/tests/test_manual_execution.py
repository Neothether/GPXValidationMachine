"""A manual test step through the API: recorded and signed by a tester, with an attachment, and by nobody else."""
import base64
import os

import pytest
from fastapi.testclient import TestClient

from app import db, runner
from app.main import app

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


def signed_in(account):
    client = TestClient(app)
    assert client.post("/api/login", json={"user_id": account, "password": os.environ["DEMO_PASSWORD"]}).status_code == 200
    return client


def test_manual_step_recorded_by_tester_only(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "EVIDENCE_DIR", tmp_path / "evidence")
    password = os.environ["DEMO_PASSWORD"]
    with db.tx("alex.author", db.APP) as conn:
        system = conn.execute("insert into systems (name, repo_path, intended_use, archived) "
                              "values ('enforcement-test', %s, 'test', true) returning id", (str(tmp_path),)).fetchone()
    with db.tx("agent:test_author", db.AGENT) as conn:
        proto = db.create_artifact(conn, system["id"], "PQ", "PQ test", "manual protocol", "agent:test_author")
        tc = conn.execute("insert into test_cases (system_id, artifact_id, test_id, title, steps, expected, automation) "
                          "values (%s, %s, 'PQ-001', 'Label prints', '1. Print a label.', 'Label shows the subject id.', 'manual') "
                          "returning id", (system["id"], proto["id"])).fetchone()
    body = {"outcome": "fail", "actual": "The label printed without the subject id.", "password": password,
            "attachment_name": "label.png", "attachment_base64": base64.b64encode(b"\x89PNG fake screenshot").decode()}
    tester, author = signed_in("terry.tester"), signed_in("alex.author")

    assert tester.post(f"/api/test-cases/{tc['id']}/manual-result", json=body).status_code == 409        # protocol not approved
    qa = signed_in("quinn.qa")
    assert qa.post(f"/api/artifacts/{proto['id']}/sign", json={"password": password, "meaning": "approved"}).status_code == 200
    assert author.post(f"/api/test-cases/{tc['id']}/manual-result", json=body).status_code == 409        # not a tester
    assert tester.post(f"/api/test-cases/{tc['id']}/manual-result", json={**body, "password": "wrong"}).status_code == 403

    recorded = tester.post(f"/api/test-cases/{tc['id']}/manual-result", json=body)
    assert recorded.status_code == 200 and recorded.json()["deviation"]                                 # a failed step opens a deviation
    listed = tester.get(f"/api/systems/{system['id']}/manual-tests").json()
    assert listed[0]["outcome"] == "fail" and listed[0]["recorded_by"] == "terry.tester"
    evidence = runner.verify_evidence(recorded.json()["execution_id"])
    assert len(evidence) == 1 and evidence[0]["intact"] and evidence[0]["path"].endswith("pq_001_attachment.png")
