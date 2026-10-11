"""The compliance behaviours must be refused by the database itself, whatever the caller does.

Runs against the TigerData service in DATABASE_URL (after `python -m app.setup`).
"""
import os

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app import db

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")

AUTHOR, QA, OWNER = "alex.author", "quinn.qa", "sam.owner"


@pytest.fixture()
def system():
    with db.tx(AUTHOR, db.APP) as conn:
        # archived: test records are kept, but hidden from the inventory
        return conn.execute("insert into systems (name, repo_path, intended_use, archived) "
                            "values ('enforcement-test', '.', 'test', true) returning *").fetchone()


def _artifact(system, kind, author="agent:spec"):
    with db.tx(author, db.AGENT) as conn:
        return db.create_artifact(conn, system["id"], kind, f"{kind} test", f"content of {kind}", author)


def _sign(artifact, signer, meaning="approved"):
    with db.tx(signer, db.APP) as conn:
        return conn.execute("insert into signatures (artifact_id, signer_id, signer_name, meaning, content_hash) "
                            "values (%s, %s, '', %s, %s) returning *",
                            (artifact["id"], signer, meaning, artifact["content_hash"])).fetchone()


def _execute(artifact, system, role=db.RUNNER, runner_id=db.RUNNER_ID):
    with db.tx(runner_id, role) as conn:
        return conn.execute("insert into executions (system_id, artifact_id, runner_id, target_fingerprint, environment) "
                            "values (%s, %s, %s, 'abc', %s) returning *",
                            (system["id"], artifact["id"], runner_id, Jsonb({}))).fetchone()


def _status(artifact):
    with db.read() as conn:
        return conn.execute("select status from artifacts where id = %s", (artifact["id"],)).fetchone()["status"]


def test_author_cannot_approve_own_document(system):
    with db.tx(QA, db.AGENT) as conn:
        own = db.create_artifact(conn, system["id"], "FS", "FS test", "content", QA)
    with pytest.raises(psycopg.errors.RaiseException, match="segregation of duties"):
        _sign(own, QA)
    assert _status(own) == "draft"


def test_agent_identity_cannot_sign(system):
    art = _artifact(system, "URS")
    with pytest.raises(psycopg.errors.RaiseException, match="only a human can sign"):
        _sign(art, "agent:reviewer")


def test_agent_role_cannot_write_signatures_results_or_evidence(system):
    art = _artifact(system, "OQ", author="agent:test_author")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.tx("agent:spec", db.AGENT) as conn:
            conn.execute("insert into signatures (artifact_id, signer_id, signer_name, meaning, content_hash) "
                         "values (%s, %s, '', 'approved', %s)", (art["id"], QA, art["content_hash"]))
    for table in ("executions", "evidence", "step_results"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with db.tx("agent:spec", db.AGENT) as conn:
                conn.execute(f"insert into {table} default values")


def test_execution_before_approval_is_rejected(system):
    proto = _artifact(system, "OQ", author="agent:test_author")
    with pytest.raises(psycopg.errors.RaiseException, match="execution before approval"):
        _execute(proto, system)


def test_approved_protocol_executes_and_records_are_append_only(system):
    proto = _artifact(system, "OQ", author="agent:test_author")
    with db.tx("agent:test_author", db.AGENT) as conn:
        tc = conn.execute("insert into test_cases (system_id, artifact_id, test_id, title, steps, expected) "
                          "values (%s, %s, 'OQ-001', 't', 's', 'e') returning *", (system["id"], proto["id"])).fetchone()
    _sign(proto, QA)
    assert _status(proto) == "approved"
    ex = _execute(proto, system)
    with db.tx(db.RUNNER_ID, db.RUNNER) as conn:
        ev = conn.execute("insert into evidence (execution_id, test_case_id, kind, path, sha256, captured_by) "
                          "values (%s, %s, 'log', 'x.log', 'deadbeef', %s) returning *",
                          (ex["id"], tc["id"], db.RUNNER_ID)).fetchone()
        conn.execute("insert into step_results (execution_id, test_case_id, outcome, actual, evidence_id, recorded_by) "
                     "values (%s, %s, 'fail', 'boom', %s, %s)", (ex["id"], tc["id"], ev["id"], db.RUNNER_ID))
    with psycopg.connect(os.environ["DATABASE_URL"]) as admin:      # even the owning role is refused
        for stmt in ("update step_results set outcome = 'pass' where execution_id = %s",
                     "update evidence set sha256 = 'tampered' where execution_id = %s",
                     "delete from evidence where execution_id = %s"):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                admin.execute(stmt, (ex["id"],))
            admin.rollback()
        with pytest.raises(psycopg.errors.RaiseException, match="frozen"):
            admin.execute("update artifacts set content = 'edited after approval' where id = %s", (proto["id"],))
        admin.rollback()


def test_result_must_come_from_the_runner_identity(system):
    proto = _artifact(system, "OQ", author="agent:test_author")
    _sign(proto, QA)
    with pytest.raises(psycopg.errors.RaiseException, match="runner identity"):
        _execute(proto, system, runner_id="agent:test_author")


def test_plan_needs_qa_and_system_owner(system):
    plan = _artifact(system, "VP", author="agent:intake")
    _sign(plan, QA)
    assert _status(plan) == "draft"
    _sign(plan, OWNER)
    assert _status(plan) == "approved"


def test_tester_cannot_approve(system):
    art = _artifact(system, "URS")
    with pytest.raises(psycopg.errors.RaiseException, match="cannot approve"):
        _sign(art, "terry.tester")


def test_system_cannot_be_validated_without_approved_summary_report(system):
    with pytest.raises(psycopg.errors.RaiseException, match="cannot be released: document_missing"):
        with db.tx(QA, db.APP) as conn:
            conn.execute("update systems set status = 'validated' where id = %s", (system["id"],))


def test_only_qa_or_owner_can_release(system):
    with pytest.raises(psycopg.errors.RaiseException, match="only Quality Assurance or the System Owner"):
        with db.tx(AUTHOR, db.APP) as conn:
            conn.execute("update systems set status = 'validated' where id = %s", (system["id"],))


def test_summary_report_cannot_be_approved_against_open_blockers(system):
    vsr = _artifact(system, "VSR", author="agent:vsr")
    with pytest.raises(psycopg.errors.RaiseException, match="cannot be approved while release blockers remain"):
        _sign(vsr, QA)


def test_manual_result_needs_a_human_tester_and_the_tester_path(system):
    proto = _artifact(system, "PQ", author="agent:test_author")
    with db.tx("agent:test_author", db.AGENT) as conn:
        tc = conn.execute("insert into test_cases (system_id, artifact_id, test_id, title, steps, expected, automation) "
                          "values (%s, %s, 'PQ-001', 't', 's', 'e', 'manual') returning *", (system["id"], proto["id"])).fetchone()
    _sign(proto, QA)
    ex = _execute(proto, system)        # the runner identity, through the runner role
    with pytest.raises(psycopg.errors.RaiseException, match="must be recorded by a human tester"):
        with db.tx(db.RUNNER_ID, db.RUNNER) as conn:
            ev = conn.execute("insert into evidence (execution_id, test_case_id, kind, path, sha256, captured_by) "
                              "values (%s, %s, 'log', 'x', 'y', %s) returning *", (ex["id"], tc["id"], db.RUNNER_ID)).fetchone()
            conn.execute("insert into step_results (execution_id, test_case_id, outcome, actual, evidence_id, recorded_by) "
                         "values (%s, %s, 'pass', 'ok', %s, %s)", (ex["id"], tc["id"], ev["id"], db.RUNNER_ID))
    ex2 = _execute(proto, system, role=db.TESTER, runner_id="terry.tester")
    with db.tx("terry.tester", db.TESTER) as conn:
        ev = conn.execute("insert into evidence (execution_id, test_case_id, kind, path, sha256, captured_by) "
                          "values (%s, %s, 'observation', 'x', 'y', 'terry.tester') returning *", (ex2["id"], tc["id"])).fetchone()
        conn.execute("insert into step_results (execution_id, test_case_id, outcome, actual, evidence_id, recorded_by) "
                     "values (%s, %s, 'pass', 'observed', %s, 'terry.tester')", (ex2["id"], tc["id"], ev["id"]))


def test_deviation_acceptance_needs_qa_and_a_justification(system):
    proto = _artifact(system, "OQ", author="agent:test_author")
    with db.tx("agent:test_author", db.AGENT) as conn:
        tc = conn.execute("insert into test_cases (system_id, artifact_id, test_id, title, steps, expected) "
                          "values (%s, %s, 'OQ-001', 't', 's', 'e') returning *", (system["id"], proto["id"])).fetchone()
    _sign(proto, QA)
    ex = _execute(proto, system)
    with db.tx(db.RUNNER_ID, db.RUNNER) as conn:
        ev = conn.execute("insert into evidence (execution_id, test_case_id, kind, path, sha256, captured_by) "
                          "values (%s, %s, 'log', 'x', 'y', %s) returning *", (ex["id"], tc["id"], db.RUNNER_ID)).fetchone()
        sr = conn.execute("insert into step_results (execution_id, test_case_id, outcome, actual, evidence_id, recorded_by) "
                          "values (%s, %s, 'fail', 'boom', %s, %s) returning *", (ex["id"], tc["id"], ev["id"], db.RUNNER_ID)).fetchone()
        dev = conn.execute("insert into deviations (system_id, step_result_id, test_case_id, description) "
                           "values (%s, %s, %s, 'failed') returning *", (system["id"], sr["id"], tc["id"])).fetchone()
    accept = "update deviations set status = 'accepted', resolution = %s, resolved_by = %s where id = %s"
    with pytest.raises(psycopg.errors.RaiseException, match="only Quality Assurance can accept"):
        with db.tx(OWNER, db.APP) as conn:
            conn.execute(accept, ("Known upstream documentation discrepancy, no impact.", OWNER, dev["id"]))
    with pytest.raises(psycopg.errors.RaiseException, match="written justification"):
        with db.tx(QA, db.APP) as conn:
            conn.execute(accept, ("ok", QA, dev["id"]))
    with db.tx(QA, db.APP) as conn:
        conn.execute(accept, ("Known upstream documentation discrepancy, no impact on intended use.", QA, dev["id"]))


def test_audit_trail_is_append_only_and_chain_verifies(system):
    with psycopg.connect(os.environ["DATABASE_URL"]) as admin:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            admin.execute("delete from audit_events where entity_id = %s", (str(system["id"]),))
        admin.rollback()
        ok, checked, bad = admin.execute("select * from vm_verify_audit_chain()").fetchone()
    assert ok and checked > 0 and bad is None
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.tx("agent:spec", db.AGENT) as conn:
            conn.execute("insert into audit_events (actor, action, entity_type, entity_id) values ('x','x','x','x')")
