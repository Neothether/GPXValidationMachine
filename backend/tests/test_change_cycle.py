"""One complete lifecycle through the API, against the real database, runner and enforcement rules:

    draft -> approve -> execute -> defect found -> fix -> retest -> approve -> RELEASE
    -> software changes -> release lapses and is refused -> impact assessed -> regression -> approvals -> RELEASE

Only the model is replaced, by fixed drafts, so the test is repeatable and needs no Gemini quota. Everything
else is real: the target is a copy of targets/cdm-query-bot with its planted defects, tests run under pytest,
and every refusal comes from the database.
"""
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from app import agents, config, db, llm, review, runner
from app.main import app

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")

REQS = dict(
    urs=[agents.Req(req_id="URS-001", statement="Readings indicating a hypertensive crisis are flagged.", source_ref="README.md:8-9"),
         agents.Req(req_id="URS-002", statement="Each flagged reading produces a draft query for the investigator.", source_ref="README.md:19-21")],
    fs=[agents.Req(req_id="FS-001", statement="Readings in kPa are converted to mmHg before thresholds are applied.", source_ref="README.md:11-12", parent="URS-001"),
        agents.Req(req_id="FS-002", statement="Systolic pressure greater than 180 mmHg is flagged; exactly 180 is not.", source_ref="README.md:8-10", parent="URS-001"),
        agents.Req(req_id="FS-003", statement="A draft query names the subject and the visit.", source_ref="README.md:19-20", parent="URS-002")],
    ds=[agents.Req(req_id="DS-001", statement="SYSTOLIC_THRESHOLD_MMHG is configured as 180.", source_ref="cdm_query_bot/config.py:3", parent="FS-002")])

READING = "{'subject_id': 'S1', 'visit': 'V1', 'systolic': %s, 'diastolic': %s%s}"
TESTS = [
    ("IQ-001", "IQ", ["DS-001"], "from cdm_query_bot import config\ndef test_threshold():\n    assert config.SYSTOLIC_THRESHOLD_MMHG == 180\n"),
    ("OQ-001", "OQ", ["FS-001"], "from cdm_query_bot import detector\ndef test_kpa():\n    assert len(detector.detect([%s])) == 1\n" % (READING % (26.0, 10.0, ", 'unit': 'kPa'"))),
    ("OQ-002", "OQ", ["FS-002"], "from cdm_query_bot import detector\ndef test_boundary():\n    assert len(detector.detect([%s])) == 1\n    assert len(detector.detect([%s])) == 0\n" % (READING % (181, 80, ""), READING % (180, 80, ""))),
    ("OQ-003", "OQ", ["FS-003"], "from cdm_query_bot import queries\nfrom cdm_query_bot.detector import Anomaly\ndef test_text():\n    q = queries.draft_query(Anomaly('S9', 'V3', 190, 80, 'high'))\n    assert 'S9' in q.text and 'V3' in q.text\n"),
    ("PQ-001", "PQ", ["URS-001"], "from cdm_query_bot import detector\ndef test_flagged():\n    assert len(detector.detect([%s])) == 1\n" % (READING % (190, 80, ""))),
    ("PQ-002", "PQ", ["URS-002"], "from cdm_query_bot import detector, queries\ndef test_draft():\n    a = detector.detect([%s])\n    assert queries.draft_query(a[0]).status == 'draft'\n" % (READING % (190, 80, ""))),
]
READ_TWO = ('        systolic = _to_number(reading.get("systolic"), "systolic", reading)\n'
            '        diastolic = _to_number(reading.get("diastolic"), "diastolic", reading)\n')
FIX = agents.FixProposal(
    investigation="kPa readings are compared with mmHg thresholds without conversion (cdm_query_bot/detector.py:30-31).",
    root_cause_location="cdm_query_bot/detector.py:30-31", file="cdm_query_bot/detector.py", is_test_defect=False,
    original_snippet=READ_TWO,
    replacement_snippet=READ_TWO + '        if str(reading.get("unit", "mmHg")).lower() == "kpa":\n'
                                   '            systolic, diastolic = systolic * config.KPA_TO_MMHG, diastolic * config.KPA_TO_MMHG\n')
CHANGE_DIFF = """--- a/cdm_query_bot/config.py
+++ b/cdm_query_bot/config.py
@@ -3,7 +3,7 @@
 SYSTOLIC_THRESHOLD_MMHG = 180
 DIASTOLIC_THRESHOLD_MMHG = 120

-KPA_TO_MMHG = 7.50062
+KPA_TO_MMHG = 7.5006

 AUDIT_ENABLED = False
 TIMEZONE = "UTC"
"""


def fake_model(agent, system, prompt, schema, tier="pro"):
    drafts = {
        agents.Profile: lambda: agents.Profile(
            gxp_impact="direct", gxp_rationale="Output reaches investigators.", gamp_category=5, category_rationale="Custom code.",
            system_description="Flags blood-pressure readings.", scope=["Detection"], out_of_scope=["Hosting"],
            deliverables=["URS", "FS", "DS", "RA", "IQ", "OQ", "PQ", "VSR"], roles_and_approvals=["QA approves"],
            acceptance_criteria=["All tests pass"]),
        agents.Specs: lambda: agents.Specs(discrepancies=[], **REQS),
        agents.RiskAssessment: lambda: agents.RiskAssessment(items=[
            agents.RiskItem(req_id=r.req_id, severity="high", probability="medium", detectability="low",
                            rationale="A missed reading delays recognition of an adverse event.", citation="GAMP 5 2nd ed. Appendix M3")
            for r in REQS["fs"]]),
        agents.Protocols: lambda: agents.Protocols(tests=[
            agents.TestCase(test_id=tid, protocol=kind, title=f"Verify {reqs[0]}", requirement_ids=reqs, steps="1. Run the test.",
                            expected="The assertion holds.", automation="pytest", code=code) for tid, kind, reqs, code in TESTS]),
        agents.FixProposal: lambda: FIX,
        review.ChangeImpact: lambda: review.ChangeImpact(
            summary="Rounds the kPa conversion factor.", risk_of_change="medium", not_impacted_rationale="Query drafting does not use the factor.",
            impacted=[review.Impact(req_id="FS-001", reason="The conversion factor feeds the kPa conversion.")]),
    }
    return drafts[schema](), {"agent": agent, "model": "fixed-test-drafts", "prompt_sha256": db.sha256(prompt), "output_sha256": "n/a"}


def signed_in(account):
    client = TestClient(app)
    assert client.post("/api/login", json={"user_id": account, "password": os.environ["DEMO_PASSWORD"]}).status_code == 200
    return client


def ok(response):
    assert response.status_code == 200, f"{response.request.method} {response.request.url.path}: {response.status_code} {response.text[:400]}"
    return response.json()


def test_full_lifecycle_and_change_cycle(tmp_path, monkeypatch):
    target = tmp_path / "target"
    shutil.copytree(config.ROOT / "targets" / "cdm-query-bot", target)
    monkeypatch.setattr(llm, "generate", fake_model)
    monkeypatch.setattr(runner, "EVIDENCE_DIR", tmp_path / "evidence")
    author, qa, owner = signed_in("alex.author"), signed_in("quinn.qa"), signed_in("sam.owner")
    password = os.environ["DEMO_PASSWORD"]

    sid = ok(author.post("/api/systems", json={"name": "cycle-test", "repo_path": str(target), "intended_use": "Flags readings for investigators.", "sop": ""}))["id"]
    try:
        def blockers():
            return {b["code"] for b in ok(qa.get(f"/api/systems/{sid}/release-check"))["blockers"]}

        def current(kind):
            return next(a for a in ok(qa.get(f"/api/systems/{sid}"))["artifacts"] if a["kind"] == kind and a["current"])

        def approve(kind, *signers):
            for client in signers:
                ok(client.post(f"/api/artifacts/{current(kind)['id']}/sign", json={"password": password, "meaning": "approved"}))

        def execute_all():
            return [ok(qa.post(f"/api/artifacts/{current(k)['id']}/execute", json={})) for k in ("IQ", "OQ", "PQ")]

        def release(client=qa):
            return client.post(f"/api/systems/{sid}/release", json={})

        # --- draft, approve, execute -----------------------------------------------------------------
        for stage in ("plan", "specs", "risk", "protocols"):
            ok(author.post(f"/api/systems/{sid}/run/{stage}", json={}))
        assert author.post(f"/api/artifacts/{current('OQ')['id']}/execute", json={}).status_code == 409   # not approved yet
        approve("VP", qa, owner)
        for kind in ("URS", "FS", "DS", "RA", "IQ", "OQ", "PQ"):
            approve(kind, qa)
        runs = execute_all()
        assert sum(r["fail"] for r in runs) == 1 and sum(r["pass"] for r in runs) == 5      # the planted kPa defect
        assert {"test_failing", "deviation_open", "summary_missing"} <= blockers()
        assert release().status_code == 409

        # --- deviation: investigate, fix, retest, close ----------------------------------------------
        dev = ok(qa.get(f"/api/systems/{sid}/deviations"))[0]
        assert dev["test_id"] == "OQ-001"
        ok(qa.post(f"/api/deviations/{dev['id']}/investigate", json={}))
        assert qa.post(f"/api/deviations/{dev['id']}/resolve", json={"resolution": "no retest", "retest_execution_id": runs[1]["execution_id"]}).status_code == 409
        ok(author.post(f"/api/deviations/{dev['id']}/apply-fix", json={}))
        retest = ok(qa.post(f"/api/deviations/{dev['id']}/retest", json={}))
        assert retest["pass"] == 1
        ok(qa.post(f"/api/deviations/{dev['id']}/resolve", json={"resolution": "kPa conversion added; retest passed.", "retest_execution_id": retest["execution_id"]}))

        # The fix changed the software, so results recorded before it no longer cover this version.
        assert "result_outdated" in blockers()
        assert all(r["fail"] == 0 for r in execute_all())
        assert blockers() == {"summary_missing"}

        # --- summary report and first release --------------------------------------------------------
        ok(author.post(f"/api/systems/{sid}/run/vsr", json={}))
        approve("VSR", qa)
        assert release().status_code == 409                                  # the system owner has not signed
        approve("VSR", owner)
        assert release(author).status_code == 409                            # only QA or the owner may release
        first = ok(release())
        check = ok(qa.get(f"/api/systems/{sid}/release-check"))
        assert check["status"] == "validated" and check["ready"] and not check["drift"]
        assert len(ok(qa.get(f"/api/systems/{sid}/snapshots"))) == 1         # point-in-time package for this version

        # --- the software changes --------------------------------------------------------------------
        cfg = target / "cdm_query_bot" / "config.py"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace("7.50062", "7.5006"), encoding="utf-8")
        check = ok(qa.get(f"/api/systems/{sid}/release-check"))
        assert check["drift"] and check["status"] == "change_pending"       # the old release does not cover the new version
        assert "software_changed" in blockers()
        assert release().status_code == 409

        change = ok(author.post(f"/api/systems/{sid}/changes", json={"kind": "code_diff", "description": "Round kPa factor", "diff": CHANGE_DIFF}))
        scope = {t["test_id"] for t in change["impact"]["regression_scope"]}
        assert scope == {"IQ-001", "OQ-001", "OQ-002", "PQ-001"}             # OQ-003 and PQ-002 are untouched by the change
        assert {"change_not_regressed", "change_unapproved"} <= blockers()
        assert release().status_code == 409

        regression = ok(qa.post(f"/api/changes/{change['change_id']}/regress", json={}))
        assert sum(r["pass"] for r in regression["executions"]) == 4 and sum(r["fail"] for r in regression["executions"]) == 0
        approve("CHANGE", qa)
        # Out-of-scope tests keep their earlier results; nothing is outdated. Only the summary must be renewed.
        assert blockers() == {"summary_outdated"}
        assert release().status_code == 409                                  # the old approved report cannot release the new version

        ok(author.post(f"/api/systems/{sid}/run/vsr", json={}))
        approve("VSR", qa, owner)
        second = ok(release())
        assert second["fingerprint"] != first["fingerprint"]
        check = ok(qa.get(f"/api/systems/{sid}/release-check"))
        assert check["status"] == "validated" and check["ready"] and len(check["releases"]) == 2
        assert len(ok(qa.get(f"/api/systems/{sid}/snapshots"))) == 2

        # --- records that follow from the cycle ------------------------------------------------------
        answers = ok(qa.get(f"/api/systems/{sid}/inspection"))
        assert "Released as validated" in answers[0]["answer"] and "all match" in answers[6]["answer"]
        periodic = ok(qa.post(f"/api/systems/{sid}/periodic-review", json={}))
        assert "remains in a validated state" in periodic["content"]
        assert ok(qa.get("/api/audit/verify"))["ok"]
    finally:
        with db.tx("alex.author", db.APP) as conn:      # keep the records, hide them from the inventory
            conn.execute("update systems set archived = true where id = %s", (sid,))
