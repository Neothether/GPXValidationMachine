"""Deterministic test runner. No LLM is involved in executing tests or recording results.

It is the only component that connects as vm_runner, the only role allowed to write evidence and results.
"""
import os
import platform
import subprocess
import sys
from pathlib import Path

from psycopg.types.json import Jsonb

from . import db, ingest

EVIDENCE_DIR = Path(os.environ.get("EVIDENCE_DIR", "./evidence")).resolve()
TIMEOUT_S = 120


def _environment(repo_path: str) -> dict:
    return {"python": sys.version.split()[0], "platform": platform.platform(), "host": platform.node(),
            "repo_path": str(Path(repo_path).resolve())}


def _run_one(repo_path: str, test_file: Path, log_file: Path) -> tuple[str, str]:
    """Run one test module in a subprocess. Outcome comes from pytest's exit code alone."""
    env = {**os.environ, "PYTHONPATH": str(Path(repo_path).resolve()), "PYTHONDONTWRITEBYTECODE": "1"}
    for secret in ("DATABASE_URL", "GEMINI_API_KEYS", "DEMO_PASSWORD"):
        env.pop(secret, None)   # generated test code never sees the tool's credentials
    cmd = [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short", "-p", "no:cacheprovider",
           "--rootdir", str(test_file.parent)]
    try:
        proc = subprocess.run(cmd, cwd=repo_path, env=env, capture_output=True, text=True, timeout=TIMEOUT_S)
        output, code = proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        output, code = f"{e.stdout or ''}\nTIMEOUT after {TIMEOUT_S}s", -1
    log_file.write_text(f"$ {' '.join(cmd)}\n(cwd: {repo_path})\n\n{output}\n\npytest exit code: {code}\n",
                        encoding="utf-8")
    outcome = "pass" if code == 0 else "fail" if code == 1 else "error"
    return outcome, output


def execute(artifact_id, retest_of=None, only_test_case_ids: list | None = None) -> dict:
    """Execute an approved protocol. The database rejects this if the protocol is not approved."""
    actor = db.RUNNER_ID
    with db.read() as conn:
        art = conn.execute("select a.*, s.repo_path from artifacts a join systems s on s.id = a.system_id "
                           "where a.id = %s", (artifact_id,)).fetchone()
        tests = conn.execute("select * from test_cases where artifact_id = %s order by test_id", (artifact_id,)).fetchall()
    if only_test_case_ids is not None:
        wanted = {str(i) for i in only_test_case_ids}
        tests = [t for t in tests if str(t["id"]) in wanted]

    with db.tx(actor, db.RUNNER) as conn:
        ex = conn.execute(
            """insert into executions (system_id, artifact_id, runner_id, target_fingerprint, environment, retest_of)
               values (%s, %s, %s, %s, %s, %s) returning *""",
            (art["system_id"], artifact_id, actor, ingest.fingerprint(art["repo_path"]),
             Jsonb(_environment(art["repo_path"])), retest_of)).fetchone()

    run_dir = EVIDENCE_DIR / str(ex["id"])
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = {"execution_id": str(ex["id"]), "pass": 0, "fail": 0, "error": 0, "deviations": []}
    for t in tests:
        stem = t["test_id"].lower().replace("-", "_")
        test_file, log_file = run_dir / f"test_{stem}.py", run_dir / f"{stem}.log"
        if t["automation"] != "pytest" or not t["code"]:
            continue    # manual steps are executed and signed by a human tester, never by the runner
        test_file.write_text(t["code"], encoding="utf-8")
        outcome, output = _run_one(art["repo_path"], test_file, log_file)
        summary[outcome] += 1
        # Each step commits on its own, so evidence is contemporaneous and survives a later crash.
        with db.tx(actor, db.RUNNER) as conn:
            ev = conn.execute(
                """insert into evidence (execution_id, test_case_id, kind, path, sha256, captured_by)
                   values (%s, %s, 'pytest_log', %s, %s, %s) returning id""",
                (ex["id"], t["id"], str(log_file), db.sha256(log_file.read_bytes()), actor)).fetchone()
            sr = conn.execute(
                """insert into step_results (execution_id, test_case_id, outcome, actual, evidence_id, recorded_by)
                   values (%s, %s, %s, %s, %s, %s) returning id""",
                (ex["id"], t["id"], outcome, output[-6000:], ev["id"], actor)).fetchone()
            if outcome != "pass":
                dev = conn.execute(
                    """insert into deviations (system_id, step_result_id, test_case_id, description)
                       values (%s, %s, %s, %s) returning id""",
                    (art["system_id"], sr["id"], t["id"],
                     f"{t['test_id']} ({t['title']}) did not meet its acceptance criteria: outcome '{outcome}'.")).fetchone()
                summary["deviations"].append(str(dev["id"]))
    with db.tx(actor, db.RUNNER) as conn:
        conn.execute("update executions set finished_at = clock_timestamp() where id = %s", (ex["id"],))
    return summary


def verify_evidence(execution_id) -> list[dict]:
    """Re-hash evidence files on disk against the hashes recorded at capture time."""
    with db.read() as conn:
        rows = conn.execute("select id, path, sha256 from evidence where execution_id = %s", (execution_id,)).fetchall()
    out = []
    for r in rows:
        p = Path(r["path"])
        actual = db.sha256(p.read_bytes()) if p.exists() else None
        out.append({"evidence_id": str(r["id"]), "path": r["path"], "intact": actual == r["sha256"]})
    return out
