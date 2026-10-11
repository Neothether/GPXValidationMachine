"""Command-line harness, for developers and CI pipelines.

    python vm.py validate --repo . --name "My app" --intended-use intended_use.txt --sop sop.md
    python vm.py execute  --system <id>
    python vm.py change   --system <id> --diff change.diff --description "What changed" --regress
    python vm.py check    --system <id>          # exit 0 only if the software on disk is released as validated
    python vm.py package  --system <id> --out package.html

It drafts, executes and assesses. It cannot approve, sign or release: those need a person in the web app,
and the database refuses them from this identity.
"""
import argparse
import os
import sys
from pathlib import Path

from . import agents, db, ingest, lifecycle, package, review, runner

ACTOR = "ci:pipeline"
CWD = Path(os.environ.get("VM_CWD", "."))


def _path(value: str) -> Path:
    p = Path(value)
    return (p if p.is_absolute() else CWD / p).resolve()


def _use_repo(a) -> None:
    """In a pipeline the repository is checked out at a different path than where the system was registered."""
    if getattr(a, "repo", None) and hasattr(a, "system"):
        with db.tx(ACTOR, db.APP) as conn:
            conn.execute("update systems set repo_path = %s where id = %s and repo_path <> %s",
                         (str(_path(a.repo)), a.system, str(_path(a.repo))))


def _current_protocols(system_id) -> list[dict]:
    with db.read() as conn:
        return conn.execute("select id, kind, version, status from v_current_artifacts where system_id = %s "
                            "and kind in ('IQ','OQ','PQ') order by kind", (system_id,)).fetchall()


def validate(a) -> int:
    repo = _path(a.repo)
    if not repo.is_dir():
        print(f"Repository not found: {repo}", file=sys.stderr)
        return 2
    sop = _path(a.sop).read_text(encoding="utf-8") if a.sop else None
    with db.tx(ACTOR, db.APP) as conn:
        system_id = conn.execute("insert into systems (name, repo_path, intended_use, sop) values (%s, %s, %s, %s) returning id",
                                 (a.name, str(repo), _path(a.intended_use).read_text(encoding="utf-8"), sop)).fetchone()["id"]
    print(f"Registered system {system_id}")
    for label, stage in (("validation plan", agents.run_intake), ("URS / FS / DS", agents.run_specs),
                         ("risk assessment", agents.run_risk), ("IQ / OQ / PQ protocols", agents.run_protocols)):
        stage(system_id)
        print(f"  drafted {label}")
    print("Drafts are ready. A person must now review and e-sign them in the web app before tests can run.")
    return 0


def execute(a) -> int:
    failed = False
    for p in _current_protocols(a.system):
        if p["status"] != "approved":
            print(f"{p['kind']} v{p['version']}: not approved, skipped (execution before approval is not permitted)")
            failed = True
            continue
        r = runner.execute(p["id"])
        print(f"{p['kind']} v{p['version']}: {r['pass']} passed, {r['fail']} failed, {r['error']} errors, "
              f"{len(r['deviations'])} deviation(s) opened")
        failed = failed or r["fail"] or r["error"]
    return 1 if failed else 0


def change(a) -> int:
    diff = sys.stdin.read() if a.diff == "-" else _path(a.diff).read_text(encoding="utf-8")
    with db.tx(ACTOR, db.APP) as conn:
        change_id = conn.execute("insert into changes (system_id, kind, description, diff) values (%s, %s, %s, %s) returning id",
                                 (a.system, a.kind, a.description, diff)).fetchone()["id"]
        conn.execute("update systems set status = 'change_pending' where id = %s", (a.system,))
    impact = review.assess_change(change_id)
    print(f"Change {change_id}: risk {impact['risk_of_change']}. {impact['summary']}")
    for r in impact["impacted_requirements"]:
        print(f"  impacts {r['req_id']}: {r['reason']}")
    print(f"Regression scope: {len(impact['regression_scope'])} of {impact['total_tests']} tests")
    failed = False
    if a.regress:
        by_protocol: dict[str, list[str]] = {}
        for t in impact["regression_scope"]:
            by_protocol.setdefault(t["artifact_id"], []).append(t["test_case_id"])
        for artifact_id, ids in by_protocol.items():
            r = runner.execute(artifact_id, only_test_case_ids=ids)
            print(f"  regression: {r['pass']} passed, {r['fail']} failed, {r['error']} errors")
            failed = failed or r["fail"] or r["error"]
        fingerprint = ingest.refresh_fingerprint(a.system, ACTOR)
        with db.tx(ACTOR, db.APP) as conn:
            conn.execute("update changes set regressed_at = clock_timestamp(), fingerprint_after = %s where id = %s",
                         (fingerprint, change_id))
    print("The change record now needs QA approval, and a renewed summary report, before release.")
    return 1 if failed else 0


def check(a) -> int:
    c = lifecycle.release_check(a.system, ACTOR)
    print(f"Status: {c['status']}    software fingerprint: {(c['fingerprint'] or 'unknown')[:16]}")
    for row in c["checklist"]:
        print(f"  [{'ok' if row['ok'] else '  '}] {row['label']}")
        for b in row["items"]:
            print(f"        {b['ref']}: {b['detail']}")
    released = c["status"] == "validated" and not c["drift"]
    print("This software version is released as validated." if released else
          "This software version is NOT released as validated.")
    return 0 if released else 3


def export_package(a) -> int:
    out = _path(a.out)
    out.write_text(package.render(a.system), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="vm", description="The Validation Machine: command-line harness")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate", help="register a repository and draft plan, specifications, risk assessment and protocols")
    p.add_argument("--repo", required=True); p.add_argument("--name", required=True)
    p.add_argument("--intended-use", required=True, help="text file with the intended-use statement")
    p.add_argument("--sop", help="text file with the company CSV SOP")
    p.set_defaults(run=validate)
    p = sub.add_parser("execute", help="execute every approved protocol and capture evidence")
    p.add_argument("--system", required=True); p.add_argument("--repo", help="path of this checkout, if it differs from where the system was registered"); p.set_defaults(run=execute)
    p = sub.add_parser("change", help="assess the impact of a diff and optionally run the regression scope")
    p.add_argument("--system", required=True); p.add_argument("--diff", required=True, help="diff file, or - for stdin")
    p.add_argument("--description", required=True)
    p.add_argument("--kind", default="code_diff", choices=("code_diff", "config", "vendor_release"))
    p.add_argument("--regress", action="store_true")
    p.add_argument("--repo", help="path of this checkout, if it differs from where the system was registered"); p.set_defaults(run=change)
    p = sub.add_parser("check", help="release checklist; exit 0 only if this software version is released as validated")
    p.add_argument("--system", required=True); p.add_argument("--repo", help="path of this checkout, if it differs from where the system was registered"); p.set_defaults(run=check)
    p = sub.add_parser("package", help="write the validation package as HTML")
    p.add_argument("--system", required=True); p.add_argument("--out", required=True); p.set_defaults(run=export_package)
    args = parser.parse_args()
    if args.command != "validate":
        _use_repo(args)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
