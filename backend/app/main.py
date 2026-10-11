"""HTTP API. Humans act through vm_app; agents through vm_agent; the runner through vm_runner."""
import secrets
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import agents, config, db, package, review, runner
from psycopg_pool import PoolTimeout

app = FastAPI(title="The Validation Machine")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])

SESSIONS: dict[str, dict] = {}


@app.exception_handler(psycopg.errors.RaiseException)
async def enforcement_rejected(_, exc):
    """A database trigger refused the action. Surface its reason verbatim."""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={"detail": exc.diag.message_primary, "enforced_by": "database"})


@app.exception_handler(psycopg.errors.InsufficientPrivilege)
async def privilege_rejected(_, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=403, content={"detail": exc.diag.message_primary, "enforced_by": "database"})


@app.exception_handler(config.ConfigurationError)
async def configuration_error(_, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={"detail": str(exc)})

@app.exception_handler(PoolTimeout)
@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(_, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={"detail":
        "Cannot connect to the database. Run python manage.py check from the repository, then restart the server."})

@app.exception_handler(psycopg.errors.UndefinedTable)
async def database_not_initialized(_, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={"detail":
        "Database schema is missing. For a new database, run python manage.py init-db. For a shared database, ask its owner to check setup."})


def _check_password(conn, user_id: str, password: str) -> dict | None:
    return conn.execute(
        "select id, display_name, role from users where id = %s and is_human "
        "and password_hash = crypt(%s, password_hash)", (user_id, password)).fetchone()


def current_user(x_token: str = Header(default="")) -> dict:
    user = SESSIONS.get(x_token)
    if not user:
        raise HTTPException(401, "Sign in first")
    return user


# --------------------------------------------------------------------------- identity
class Login(BaseModel):
    user_id: str
    password: str


@app.post("/api/login")
def login(body: Login):
    with db.read() as conn:
        user = _check_password(conn, body.user_id, body.password)
    if not user:
        raise HTTPException(401, "Unknown user or wrong password")
    token = secrets.token_urlsafe(24)
    SESSIONS[token] = user
    return {"token": token, "user": user}


@app.get("/api/users")
def users():
    with db.read() as conn:
        return conn.execute("select id, display_name, role, is_human from users order by is_human desc, id").fetchall()


@app.get("/api/demo")
def demo_fixtures():
    """Synthetic SOP, intended-use statements and change stream for the bundled demo target."""
    root = Path(__file__).resolve().parents[2]
    fx = root / "fixtures"
    return {"name": "CDM Query Bot", "repo_path": str(root / "targets" / "cdm-query-bot"),
            "sop": (fx / "sop.md").read_text(encoding="utf-8"),
            "intended_use": {p.stem.replace("intended_use_", ""): p.read_text(encoding="utf-8")
                             for p in sorted(fx.glob("intended_use_*.md"))},
            "changes": {p.stem: p.read_text(encoding="utf-8") for p in sorted((fx / "changes").glob("*.diff"))}}


# --------------------------------------------------------------------------- systems (inventory)
class NewSystem(BaseModel):
    name: str
    repo_path: str
    intended_use: str
    sop: str | None = None


@app.post("/api/systems")
def create_system(body: NewSystem, user=Depends(current_user)):
    if not Path(body.repo_path).is_dir():
        raise HTTPException(400, f"Repository path not found: {body.repo_path}")
    with db.tx(user["id"], db.APP) as conn:
        return conn.execute("insert into systems (name, repo_path, intended_use, sop) values (%s, %s, %s, %s) returning *",
                            (body.name, body.repo_path, body.intended_use, body.sop)).fetchone()


@app.get("/api/systems")
def list_systems():
    with db.read() as conn:
        return conn.execute(
            """select s.*, (select count(*) from v_gaps g where g.system_id = s.id) as open_gaps,
                      (select count(*) from deviations d where d.system_id = s.id and d.status = 'open') as open_deviations,
                      (select max(started_at) from executions e where e.system_id = s.id) as last_executed
                 from systems s order by created_at desc""").fetchall()


@app.get("/api/systems/{sid}")
def get_system(sid: str):
    with db.read() as conn:
        s = conn.execute("select * from systems where id = %s", (sid,)).fetchone()
        if not s:
            raise HTTPException(404)
        s["artifacts"] = conn.execute(
            """select a.id, a.kind, a.version, a.title, a.status, a.author_id, a.created_at, a.content_hash,
                      a.id in (select id from v_current_artifacts) as current
                 from artifacts a where system_id = %s order by created_at""", (sid,)).fetchall()
        return s


@app.post("/api/systems/{sid}/status/{status}")
def set_status(sid: str, status: str, user=Depends(current_user)):
    with db.tx(user["id"], db.APP) as conn:
        return conn.execute("update systems set status = %s where id = %s returning *", (status, sid)).fetchone()


# --------------------------------------------------------------------------- pipeline stages (agents draft)
STAGES = {
    "plan": agents.run_intake,
    "specs": agents.run_specs,
    "risk": agents.run_risk,
    "protocols": agents.run_protocols,
    "review": review.run_review,
    "vsr": review.draft_vsr,
}


@app.post("/api/systems/{sid}/run/{stage}")
def run_stage(sid: str, stage: str, user=Depends(current_user)):
    if stage not in STAGES:
        raise HTTPException(404, f"Unknown stage. Choose from {list(STAGES)}")
    with db.tx(user["id"], db.APP) as conn:
        db.audit(conn, "stage_requested", "system", sid, {"stage": stage})
    return db.jsonable(STAGES[stage](sid))


class ExternalPackage(BaseModel):
    text: str


@app.post("/api/systems/{sid}/review-external")
def review_external(sid: str, body: ExternalPackage, user=Depends(current_user)):
    return review.run_review(sid, external_package=body.text)


# --------------------------------------------------------------------------- documents and e-signature
@app.get("/api/artifacts/{aid}")
def get_artifact(aid: str):
    with db.read() as conn:
        a = conn.execute("select * from artifacts where id = %s", (aid,)).fetchone()
        if not a:
            raise HTTPException(404)
        a["signatures"] = conn.execute("select * from signatures where artifact_id = %s order by signed_at", (aid,)).fetchall()
        a["test_cases"] = conn.execute(
            """select tc.id, tc.test_id, tc.title, lr.outcome, lr.recorded_at from test_cases tc
                 left join v_latest_results lr on lr.test_case_id = tc.id
                where tc.artifact_id = %s order by tc.test_id""", (aid,)).fetchall()
        return a


class Sign(BaseModel):
    password: str
    meaning: str


@app.post("/api/artifacts/{aid}/sign")
def sign(aid: str, body: Sign, user=Depends(current_user)):
    """E-signature: the signer re-enters their password; the signature binds to the exact content hash."""
    with db.tx(user["id"], db.APP) as conn:
        if not _check_password(conn, user["id"], body.password):
            raise HTTPException(401, "Password re-entry failed: signature not applied")
        a = conn.execute("select content_hash from artifacts where id = %s", (aid,)).fetchone()
        if not a:
            raise HTTPException(404)
        return conn.execute(
            "insert into signatures (artifact_id, signer_id, signer_name, meaning, content_hash) "
            "values (%s, %s, '', %s, %s) returning *", (aid, user["id"], body.meaning, a["content_hash"])).fetchone()


# --------------------------------------------------------------------------- execution, evidence, deviations
@app.post("/api/artifacts/{aid}/execute")
def execute(aid: str, user=Depends(current_user)):
    with db.tx(user["id"], db.APP) as conn:
        db.audit(conn, "execution_requested", "artifact", aid)
    return runner.execute(aid)


@app.get("/api/systems/{sid}/executions")
def executions(sid: str):
    with db.read() as conn:
        rows = conn.execute(
            """select e.*, a.kind, a.version from executions e join artifacts a on a.id = e.artifact_id
                where e.system_id = %s order by e.started_at desc""", (sid,)).fetchall()
        for e in rows:
            e["results"] = conn.execute(
                """select sr.id, tc.test_id, tc.title, sr.outcome, sr.actual, sr.recorded_at, sr.recorded_by,
                          ev.path, ev.sha256
                     from step_results sr join test_cases tc on tc.id = sr.test_case_id
                     join evidence ev on ev.id = sr.evidence_id
                    where sr.execution_id = %s order by tc.test_id""", (e["id"],)).fetchall()
        return rows


@app.get("/api/executions/{eid}/verify")
def verify_execution(eid: str):
    return runner.verify_evidence(eid)


@app.get("/api/systems/{sid}/deviations")
def deviations(sid: str):
    with db.read() as conn:
        return conn.execute(
            """select d.*, tc.test_id, tc.title, sr.actual from deviations d
                 join test_cases tc on tc.id = d.test_case_id join step_results sr on sr.id = d.step_result_id
                where d.system_id = %s order by d.opened_at""", (sid,)).fetchall()


@app.post("/api/deviations/{did}/investigate")
def investigate(did: str, user=Depends(current_user)):
    return db.jsonable(agents.run_fix_proposal(did))


@app.post("/api/deviations/{did}/apply-fix")
def apply_fix(did: str, user=Depends(current_user)):
    """A human accepts the proposed patch. The change is attributed to them, not to the agent."""
    import json
    with db.read() as conn:
        d = conn.execute("select d.*, s.repo_path from deviations d join systems s on s.id = d.system_id where d.id = %s", (did,)).fetchone()
    fix = json.loads(d["proposed_fix"] or "{}")
    if not fix.get("original_snippet") or fix.get("is_test_defect"):
        raise HTTPException(400, "No applicable patch was proposed for this deviation")
    root = Path(d["repo_path"]).resolve()
    target = (root / fix["file"]).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(400, "Proposed patch points outside the repository")
    text = target.read_text(encoding="utf-8")
    if text.count(fix["original_snippet"]) != 1:
        raise HTTPException(409, "Proposed patch does not apply cleanly; fix manually and retest")
    target.write_text(text.replace(fix["original_snippet"], fix["replacement_snippet"]), encoding="utf-8")
    with db.tx(user["id"], db.APP) as conn:
        db.audit(conn, "fix_applied", "deviation", did, {"file": fix["file"]})
    return {"applied": fix["file"]}


@app.post("/api/deviations/{did}/retest")
def retest(did: str, user=Depends(current_user)):
    with db.read() as conn:
        d = conn.execute("select d.*, tc.artifact_id, sr.execution_id from deviations d join test_cases tc on tc.id = d.test_case_id "
                         "join step_results sr on sr.id = d.step_result_id where d.id = %s", (did,)).fetchone()
    with db.tx(user["id"], db.APP) as conn:
        db.audit(conn, "retest_requested", "deviation", did)
    return runner.execute(d["artifact_id"], retest_of=d["execution_id"], only_test_case_ids=[d["test_case_id"]])


class Resolve(BaseModel):
    resolution: str
    retest_execution_id: str


@app.post("/api/deviations/{did}/resolve")
def resolve(did: str, body: Resolve, user=Depends(current_user)):
    with db.tx(user["id"], db.APP) as conn:
        return conn.execute(
            "update deviations set status = 'resolved', resolution = %s, retest_execution_id = %s, resolved_by = %s "
            "where id = %s returning *", (body.resolution, body.retest_execution_id, user["id"], did)).fetchone()


# --------------------------------------------------------------------------- traceability, review, audit
@app.get("/api/systems/{sid}/trace")
def trace(sid: str):
    with db.read() as conn:
        return conn.execute("select * from v_trace where system_id = %s order by level desc, req_id, test_id", (sid,)).fetchall()


@app.get("/api/systems/{sid}/gaps")
def gaps(sid: str):
    with db.read() as conn:
        return conn.execute("select * from v_gaps where system_id = %s order by gap, ref", (sid,)).fetchall()


@app.get("/api/systems/{sid}/risks")
def risks(sid: str):
    with db.read() as conn:
        return conn.execute(
            """select r.req_id, r.statement, k.* from risks k join requirements r on r.id = k.requirement_id
                 join v_current_artifacts a on a.id = k.artifact_id where k.system_id = %s order by r.req_id""", (sid,)).fetchall()


@app.get("/api/systems/{sid}/findings")
def findings(sid: str):
    with db.read() as conn:
        return {"findings": conn.execute("select * from findings where system_id = %s order by severity, document", (sid,)).fetchall(),
                "controls": conn.execute("select * from controls where system_id = %s order by rule", (sid,)).fetchall(),
                "audit_anomalies": conn.execute("select * from v_audit_anomalies where system_id = %s order by at", (sid,)).fetchall()}


@app.get("/api/audit")
def audit_trail(entity_id: str | None = None, limit: int = 200):
    with db.read() as conn:
        if entity_id:
            return conn.execute("select * from audit_events where entity_id = %s order by recorded_at desc limit %s", (entity_id, limit)).fetchall()
        return conn.execute("select * from audit_events order by recorded_at desc limit %s", (limit,)).fetchall()


@app.get("/api/audit/verify")
def audit_verify():
    with db.read() as conn:
        return conn.execute("select * from vm_verify_audit_chain()").fetchone()


# --------------------------------------------------------------------------- change control
class NewChange(BaseModel):
    kind: str
    description: str
    diff: str


@app.post("/api/systems/{sid}/changes")
def create_change(sid: str, body: NewChange, user=Depends(current_user)):
    with db.tx(user["id"], db.APP) as conn:
        ch = conn.execute("insert into changes (system_id, kind, description, diff) values (%s, %s, %s, %s) returning id",
                          (sid, body.kind, body.description, body.diff)).fetchone()
        conn.execute("update systems set status = 'change_pending' where id = %s", (sid,))
    return {"change_id": str(ch["id"]), "impact": review.assess_change(ch["id"])}


@app.get("/api/systems/{sid}/changes")
def list_changes(sid: str):
    with db.read() as conn:
        return conn.execute("select * from changes where system_id = %s order by created_at desc", (sid,)).fetchall()


@app.post("/api/changes/{cid}/regress")
def regress(cid: str, user=Depends(current_user)):
    """Run exactly the regression scope from the impact assessment."""
    with db.read() as conn:
        ch = conn.execute("select * from changes where id = %s", (cid,)).fetchone()
    by_protocol: dict[str, list[str]] = {}
    for t in (ch["impact"] or {}).get("regression_scope", []):
        by_protocol.setdefault(t["artifact_id"], []).append(t["test_case_id"])
    with db.tx(user["id"], db.APP) as conn:
        db.audit(conn, "regression_requested", "change", cid)
    return [runner.execute(aid, only_test_case_ids=ids) for aid, ids in by_protocol.items()]


# --------------------------------------------------------------------------- validation package
@app.get("/api/systems/{sid}/package", response_class=HTMLResponse)
def validation_package(sid: str):
    return package.render(sid)
