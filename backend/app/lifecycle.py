"""Keeping a system validated: release decision, package snapshots, audit trail review with CAPA,
periodic review and inspection answers. Everything here is built from records, with no model call,
except `ask`, which answers a free-text question from the same records."""
import csv
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import db, ingest, llm, package, runner

# How the blocker codes from vm_release_blockers() group into the checklist shown to people.
CHECKLIST = [
    ("Required documents are approved", ("document_missing", "document_unapproved")),
    ("Every requirement has a risk rating and a test", ("untested_requirement", "unassessed_risk")),
    ("The software on disk is the version that was tested", ("not_tested", "version_unknown", "software_changed")),
    ("Every test passes for this software version", ("test_not_executed", "test_failing", "result_outdated")),
    ("Deviations are closed or accepted", ("deviation_open",)),
    ("Changes are assessed, regressed and approved", ("change_not_regressed", "change_unapproved")),
    ("Review findings are dispositioned", ("finding_open",)),
    ("The summary report is current and approved", ("summary_missing", "summary_outdated", "summary_unapproved")),
]


def lapse_if_changed(system_id, actor: str) -> bool:
    """A released system whose software no longer matches the released version stops being 'validated'.
    Returns True when the software differs from the last release."""
    fingerprint = ingest.refresh_fingerprint(system_id, actor)
    with db.read() as conn:
        s = conn.execute(
            """select s.status, (select r.fingerprint from releases r where r.system_id = s.id
                                  order by r.released_at desc limit 1) as released
                 from systems s where s.id = %s""", (system_id,)).fetchone()
    changed = bool(fingerprint and s["released"] and fingerprint != s["released"])
    if changed and s["status"] == "validated":
        with db.tx(actor, db.APP) as conn:
            conn.execute("update systems set status = 'change_pending' where id = %s and status = 'validated'", (system_id,))
            db.audit(conn, "validation_lapsed", "system", system_id,
                     {"released_fingerprint": s["released"], "current_fingerprint": fingerprint})
    return changed


def release_check(system_id, actor: str) -> dict:
    """Every reason the system may not be released, for the software as it is on disk right now."""
    lapse_if_changed(system_id, actor)
    fingerprint = ingest.refresh_fingerprint(system_id, actor)
    with db.read() as conn:
        s = conn.execute("select status, repo_path from systems where id = %s", (system_id,)).fetchone()
        blockers = conn.execute("select * from vm_release_blockers(%s)", (system_id,)).fetchall()
        releases = conn.execute(
            """select r.*, u.display_name, a.version as vsr_version from releases r join users u on u.id = r.released_by
                 join artifacts a on a.id = r.vsr_artifact_id where r.system_id = %s order by r.released_at desc""",
            (system_id,)).fetchall()
    checklist = [{"label": label, "items": [b for b in blockers if b["code"] in codes]} for label, codes in CHECKLIST]
    for row in checklist:
        row["ok"] = not row["items"]
    last = releases[0] if releases else None
    return {"status": s["status"], "fingerprint": fingerprint, "repo_available": Path(s["repo_path"]).is_dir(),
            "ready": not blockers, "blockers": blockers, "checklist": checklist, "releases": releases,
            # the released version is no longer the one on disk
            "drift": bool(last and fingerprint and last["fingerprint"] != fingerprint)}


def release(system_id, user: dict) -> dict:
    """Release as validated. The database refuses unless there are no blockers and the user is QA or the owner."""
    ingest.refresh_fingerprint(system_id, user["id"])
    with db.tx(user["id"], db.APP) as conn:
        conn.execute("update systems set status = 'validated' where id = %s", (system_id,))
        rel = conn.execute("select * from releases where system_id = %s order by released_at desc limit 1",
                           (system_id,)).fetchone()
    snapshot(system_id, user, f"Release of software version {rel['fingerprint'][:12]}")
    return rel


# --------------------------------------------------------------------------- point-in-time package exports
def snapshot(system_id, user: dict, label: str) -> dict:
    """Freeze the whole validation package as it is now. Snapshots are append-only and hashed."""
    html = package.render(system_id, snapshot_label=label)
    with db.read() as conn:
        fingerprint = conn.execute("select current_fingerprint from systems where id = %s", (system_id,)).fetchone()["current_fingerprint"]
    with db.tx(user["id"], db.APP) as conn:
        return conn.execute(
            """insert into package_snapshots (system_id, label, fingerprint, html, sha256, created_by)
               values (%s, %s, %s, %s, %s, %s) returning id, label, fingerprint, sha256, created_by, created_at""",
            (system_id, label, fingerprint, html, db.sha256(html), user["id"])).fetchone()


def snapshots(system_id) -> list[dict]:
    with db.read() as conn:
        return conn.execute("select id, label, fingerprint, sha256, created_by, created_at from package_snapshots "
                            "where system_id = %s order by created_at desc", (system_id,)).fetchall()


# --------------------------------------------------------------------------- target audit trail and its review
def import_audit_trail(system_id, user: dict, csv_text: str) -> int:
    """Load the target system's audit trail from CSV. Columns: recorded_at, event_time (optional), actor,
    action, record_id; any other column (for example `author`) is kept as detail."""
    rows = []
    for r in csv.DictReader(io.StringIO(csv_text.strip())):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        recorded = datetime.fromisoformat(r.pop("recorded_at").replace("Z", "+00:00"))
        event = r.pop("event_time", "") or ""
        event_time = datetime.fromisoformat(event.replace("Z", "+00:00")) if event else recorded
        stamp = [t if t.tzinfo else t.replace(tzinfo=timezone.utc) for t in (recorded, event_time)]
        rows.append((stamp[0], stamp[1], system_id, r.pop("actor"), r.pop("action").lower(), r.pop("record_id"),
                     Jsonb({k: v for k, v in r.items() if k and v})))
    return _insert_audit_rows(system_id, user, rows, "csv")


def load_demo_audit_trail(system_id, user: dict) -> int:
    from .seed_audit import events
    return _insert_audit_rows(system_id, user, events(str(system_id)), "synthetic demo")


def _insert_audit_rows(system_id, user, rows, source) -> int:
    with db.tx(user["id"], db.APP) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """insert into target_audit_events (recorded_at, event_time, system_id, actor, action, record_id, detail)
                   values (%s, %s, %s, %s, %s, %s, %s)""", rows)
        db.audit(conn, "audit_trail_imported", "system", system_id, {"events": len(rows), "source": source})
    return len(rows)


# What to do about each kind of anomaly: (severity, corrective action, preventive action, owner role)
CAPA = {
    "back_dated_entry": ("major",
                         "Confirm the true time of the event against source documents with the site, and annotate the record with the reason for the late entry.",
                         "Make the event time system-generated and require a reason for change on every late or corrected entry.",
                         "Quality Assurance"),
    "self_approval": ("major",
                      "Have an independent approver re-review and re-approve each affected record.",
                      "Enforce in the system that the author of a record cannot approve it (authority checks, 21 CFR 11.10(g)).",
                      "System Owner"),
    "mass_delete": ("critical",
                    "Verify the deletions against an approved request; restore the records from backup if they were not authorised.",
                    "Restrict delete permission, require approval for bulk deletion, and alert on bursts of deletions.",
                    "System Owner"),
    "off_hours_change": ("minor",
                         "Confirm with the user and their manager that the off-hours activity was legitimate.",
                         "Add off-hours changes to the routine audit trail review and reconsider out-of-hours access.",
                         "Quality Assurance"),
}


def draft_audit_review(system_id) -> dict:
    """Audit trail review report with corrective and preventive actions, for QA signature."""
    actor = "agent:audit_review"
    with db.read() as conn:
        s = conn.execute("select name from systems where id = %s", (system_id,)).fetchone()
        span = conn.execute("select count(*) as n, count(distinct actor) as actors, min(recorded_at) as first, "
                            "max(recorded_at) as last from target_audit_events where system_id = %s", (system_id,)).fetchone()
        anomalies = conn.execute("select * from v_audit_anomalies where system_id = %s order by at", (system_id,)).fetchall()
    if not span["n"]:
        raise ValueError("No audit trail has been loaded for this system.")
    today = datetime.now(timezone.utc).date()
    lines = [f"# Audit Trail Review: {s['name']}", "",
             f"Reviewed {span['n']} audit trail events by {span['actors']} users, from {span['first']:%Y-%m-%d} to "
             f"{span['last']:%Y-%m-%d}. Basis: 21 CFR 11.10(e); EU GMP Annex 11 §9.",
             "", "Detection is rule-based and reproducible: back-dated entries (event time more than one hour before the "
             "entry was recorded), self-approvals, bursts of 10 or more deletions by one user within five minutes, and "
             "changes made between 01:00 and 04:59 UTC.", "",
             f"## 1. Anomalies ({len(anomalies)})", "| # | Type | User | When (UTC) | Detail |", "|---|---|---|---|---|"]
    lines += [f"| A{i} | {a['anomaly'].replace('_', ' ')} | {a['actor']} | {a['at']:%Y-%m-%d %H:%M} | {a['detail']} |"
              for i, a in enumerate(anomalies, 1)] or ["| | none | | | No anomalies detected |"]
    lines += ["", "## 2. Corrective and preventive actions (CAPA)",
              "| CAPA | Addresses | Severity | Corrective action | Preventive action | Owner | Due |", "|---|---|---|---|---|---|---|"]
    kinds = sorted({a["anomaly"] for a in anomalies}, key=lambda k: ("critical", "major", "minor").index(CAPA[k][0]))
    for n, kind in enumerate(kinds, 1):
        severity, corrective, preventive, owner = CAPA[kind]
        refs = ", ".join(f"A{i}" for i, a in enumerate(anomalies, 1) if a["anomaly"] == kind)
        due = today + timedelta(days=14 if severity == "critical" else 30 if severity == "major" else 60)
        lines.append(f"| CAPA-{n:02d} | {refs} | {severity} | {corrective} | {preventive} | {owner} | {due} |")
    if not kinds:
        lines.append("| | | | No action required | | | |")
    lines += ["", "## 3. Conclusion (draft, for QA decision)",
              "The anomalies above require investigation before the affected records can be relied on."
              if anomalies else "No anomalies were detected in the period reviewed."]
    with db.tx(actor, db.AGENT) as conn:
        return db.create_artifact(conn, system_id, "ATR", f"Audit Trail Review: {s['name']}", "\n".join(lines), actor)


# --------------------------------------------------------------------------- periodic review
OWNER_FOR = {"document": "Quality Assurance", "untested": "Quality Assurance", "unassessed": "Quality Assurance",
             "software": "System Owner", "not": "System Owner", "version": "System Owner", "test": "System Owner",
             "result": "System Owner", "deviation": "Quality Assurance", "change": "System Owner",
             "finding": "Quality Assurance", "summary": "Quality Assurance"}


def draft_periodic_review(system_id, actor_user: str) -> dict:
    """Periodic review of the validated state, with findings, owners and due dates, for QA and owner signature."""
    actor = "agent:periodic_review"
    check = release_check(system_id, actor_user)
    with db.read() as conn:
        s = conn.execute("select * from systems where id = %s", (system_id,)).fetchone()
        last_review = conn.execute("select created_at from artifacts where system_id = %s and kind = 'PR' and status = 'approved' "
                                   "order by created_at desc limit 1", (system_id,)).fetchone()
        since = last_review["created_at"] if last_review else s["created_at"]
        changes = conn.execute(
            """select c.*, a.status as record_status from changes c left join artifacts a on a.id = c.artifact_id
                where c.system_id = %s and c.created_at >= %s order by c.created_at""", (system_id, since)).fetchall()
        devs = conn.execute("select d.*, t.test_id from deviations d join test_cases t on t.id = d.test_case_id "
                            "where d.system_id = %s and d.opened_at >= %s order by d.opened_at", (system_id, since)).fetchall()
        tests = conn.execute(
            """select count(*) as total, count(*) filter (where lr.outcome = 'pass') as passing
                 from test_cases t join v_current_artifacts a on a.id = t.artifact_id
                 left join v_latest_results lr on lr.test_case_id = t.id where t.system_id = %s""", (system_id,)).fetchone()
        anomalies = conn.execute("select count(*) as n from v_audit_anomalies where system_id = %s", (system_id,)).fetchone()["n"]
        atr = conn.execute("select version, status, created_at from v_current_artifacts where system_id = %s and kind = 'ATR'",
                           (system_id,)).fetchone()
        accounts = conn.execute("select id, display_name, role from users where is_human order by id").fetchall()
    today = datetime.now(timezone.utc).date()
    months = 12 if s["gxp_impact"] == "direct" else 24
    last = check["releases"][0] if check["releases"] else None

    findings = [(f"{b['code'].replace('_', ' ')}: {b['ref']}", b["detail"], OWNER_FOR.get(b["code"].split("_")[0], "Quality Assurance"))
                for b in check["blockers"]]
    if check["drift"]:
        findings.append(("software differs from the released version",
                         f"Released fingerprint {last['fingerprint'][:12]}, current {check['fingerprint'][:12]}.", "System Owner"))
    if anomalies and (not atr or atr["status"] != "approved"):
        findings.append(("audit trail anomalies not reviewed",
                         f"{anomalies} anomalies detected and no approved audit trail review exists.", "Quality Assurance"))
    if not last:
        findings.append(("system has never been released", "No release record exists.", "System Owner"))
    remains = bool(last) and not findings

    lines = [f"# Periodic Review: {s['name']}", "",
             f"Review date {today}. Period covered: {since:%Y-%m-%d} to {today}. Review interval {months} months "
             f"(GxP impact: {s['gxp_impact'] or 'not assessed'}), per the company SOP. Basis: EU GMP Annex 11 §11.", "",
             "## 1. Validated state",
             (f"- Last release: {last['released_at']:%Y-%m-%d %H:%M} UTC by {last['display_name']}, software fingerprint "
              f"`{last['fingerprint'][:16]}`, Validation Summary Report v{last['vsr_version']}." if last else "- No release on record."),
             f"- Software fingerprint now: `{(check['fingerprint'] or 'unknown')[:16]}`"
             + (" (differs from the released version)" if check["drift"] else ""),
             f"- Current status: {s['status'].replace('_', ' ')}", "",
             f"## 2. Changes in the period ({len(changes)})", "| Registered | Change | Risk | Regressed | Record |", "|---|---|---|---|---|",
             *[f"| {c['created_at']:%Y-%m-%d} | {c['description']} | {(c['impact'] or {}).get('risk_of_change', '')} | "
               f"{'withdrawn' if c['withdrawn_at'] else 'yes' if c['regressed_at'] else 'no'} | {c['record_status'] or 'none'} |" for c in changes],
             "", f"## 3. Deviations in the period ({len(devs)})", "| Test | Status | Resolution |", "|---|---|---|",
             *[f"| {d['test_id']} | {d['status']} | {d['resolution'] or ''} |" for d in devs],
             "", "## 4. Test status", f"- {tests['passing']} of {tests['total']} current tests have a passing latest result.",
             "", "## 5. Audit trail review",
             (f"- Latest audit trail review: v{atr['version']}, {atr['status']}, {atr['created_at']:%Y-%m-%d}." if atr
              else "- No audit trail review on record."), f"- Anomalies currently detected: {anomalies}.",
             "", "## 6. Access review", "Confirm each account is still required and holds the right role.",
             "| Account | Name | Role |", "|---|---|---|", *[f"| {u['id']} | {u['display_name']} | {u['role']} |" for u in accounts],
             "", f"## 7. Findings ({len(findings)})", "| # | Finding | Detail | Owner | Due |", "|---|---|---|---|---|",
             *[f"| PR-{i:02d} | {f[0]} | {f[1]} | {f[2]} | {today + timedelta(days=30)} |" for i, f in enumerate(findings, 1)],
             "", "## 8. Conclusion (draft, for QA and System Owner decision)",
             "The records support that the system remains in a validated state." if remains else
             "The records do NOT support that the system is currently in a validated state. The findings above must be closed.",
             "", f"Next periodic review due: {today + timedelta(days=30 * months)}."]
    with db.tx(actor, db.AGENT) as conn:
        return db.create_artifact(conn, system_id, "PR", f"Periodic Review: {s['name']}", "\n".join(lines), actor)


# --------------------------------------------------------------------------- inspection readiness
def inspection(system_id, actor: str) -> list[dict]:
    """Answers to the questions an inspector asks first, each drawn from records and citing them."""
    check = release_check(system_id, actor)
    with db.read() as conn:
        s = conn.execute("select * from systems where id = %s", (system_id,)).fetchone()
        docs = conn.execute("select * from v_current_artifacts where system_id = %s order by created_at", (system_id,)).fetchall()
        sigs = conn.execute(
            """select a.kind, a.version, g.signer_name, g.meaning, g.signed_at from signatures g
                 join artifacts a on a.id = g.artifact_id where a.system_id = %s order by g.signed_at""", (system_id,)).fetchall()
        trace = conn.execute("select * from v_trace where system_id = %s and level in ('URS','FS')", (system_id,)).fetchall()
        devs = conn.execute("select d.*, t.test_id from deviations d join test_cases t on t.id = d.test_case_id "
                            "where d.system_id = %s order by d.opened_at", (system_id,)).fetchall()
        changes = conn.execute("select c.*, a.status as record_status, a.version as record_version from changes c "
                               "left join artifacts a on a.id = c.artifact_id where c.system_id = %s order by c.created_at", (system_id,)).fetchall()
        execs = conn.execute("select id from executions where system_id = %s", (system_id,)).fetchall()
        chain = conn.execute("select * from vm_verify_audit_chain()").fetchone()
        reviews = conn.execute("select kind, version, status, created_at from artifacts where system_id = %s and kind in ('PR','ATR') "
                               "order by created_at desc", (system_id,)).fetchall()
    last = check["releases"][0] if check["releases"] else None
    reqs = {t["req_id"] for t in trace}
    untested = sorted({t["req_id"] for t in trace if not t["test_id"]})
    passing = sorted({t["req_id"] for t in trace} - {t["req_id"] for t in trace if t["outcome"] != "pass"})
    evidence = [v for e in execs for v in runner.verify_evidence(e["id"])]
    altered = [v["path"] for v in evidence if not v["intact"]]
    plan = next((d for d in docs if d["kind"] == "VP"), None)
    pr = next((r for r in reviews if r["kind"] == "PR"), None)
    atr = next((r for r in reviews if r["kind"] == "ATR"), None)

    def qa(question, answer, citations):
        return {"question": question, "answer": answer, "citations": [c for c in citations if c]}

    return [
        qa("Is this system validated, for which software version, and who released it?",
           (f"Released as validated on {last['released_at']:%Y-%m-%d %H:%M} UTC by {last['display_name']}, for software "
            f"fingerprint {last['fingerprint'][:16]}. " + ("The software on disk now DIFFERS from that version, so the release "
            "does not cover it." if check["drift"] else "The software on disk now matches that version.")) if last else
           f"No. The system has never been released. {len(check['blockers'])} release blocker(s) are open.",
           [last and f"release record {last['id']}", last and f"Validation Summary Report v{last['vsr_version']}",
            f"current fingerprint {(check['fingerprint'] or 'unknown')[:16]}"]),
        qa("What is the intended use and how was the system classified?",
           f"{s['intended_use'].strip()} Classified as GxP impact '{s['gxp_impact']}', GAMP 5 category {s['gamp_category']}. {s['category_rationale'] or ''}",
           [plan and f"Validation Plan v{plan['version']} ({plan['status']})"]),
        qa("Can you show that every requirement was tested?",
           f"{len(reqs)} user and functional requirements are traced. {len(passing)} have only passing latest results. "
           + (f"Not tested: {', '.join(untested)}." if untested else "None is without a test."),
           ["traceability matrix (requirement, risk, test, result, evidence hash)",
            *[f"{d['kind']} v{d['version']} ({d['status']})" for d in docs if d["kind"] in ("IQ", "OQ", "PQ")]]),
        qa("Which tests failed, and what was done about each?",
           " ".join(f"{d['test_id']}: {d['status']}" + (f" ({d['resolution']})" if d["resolution"] else "") + "." for d in devs)
           or "No test has failed.",
           [f"deviation {d['id']}" + (f", retest execution {d['retest_execution_id']}" if d["retest_execution_id"] else "") for d in devs]),
        qa("Who approved each document, and when?",
           " ".join(f"{g['kind']} v{g['version']}: {g['signer_name']} ({g['meaning']}, {g['signed_at']:%Y-%m-%d %H:%M} UTC)." for g in sigs)
           or "No document has been signed.",
           [f"{len(sigs)} electronic signature records, each bound to the content hash of the version signed"]),
        qa("What has changed since validation, and how was each change assessed?",
           " ".join(f"{c['created_at']:%Y-%m-%d} '{c['description']}': "
                    + ("withdrawn" if c["withdrawn_at"] else
                       f"{len((c['impact'] or {}).get('regression_scope', []))} of {(c['impact'] or {}).get('total_tests', '?')} tests in regression scope, "
                       f"{'regressed' if c['regressed_at'] else 'NOT regressed'}, change record {c['record_status'] or 'missing'}") + "."
                    for c in changes) or "No change has been registered.",
           [f"change record v{c['record_version']}" for c in changes if c["record_version"]]),
        qa("How do you know the evidence and the audit trail have not been altered?",
           f"{len(evidence)} evidence files were re-hashed just now against the SHA-256 recorded at capture: "
           + (f"{len(altered)} do NOT match." if altered else "all match.")
           + f" The audit trail hash chain was re-verified over {chain['checked']} events: "
           + ("intact." if chain["ok"] else f"BROKEN at event {chain['first_bad_id']}."),
           ["evidence table (append-only, hashed)", "audit_events (append-only, hash-chained)", *altered[:5]]),
        qa("Has the system's audit trail been reviewed, and when is the next periodic review due?",
           (f"Latest audit trail review: v{atr['version']}, {atr['status']}, {atr['created_at']:%Y-%m-%d}. " if atr
            else "No audit trail review is on record. ")
           + (f"Latest periodic review: v{pr['version']}, {pr['status']}, {pr['created_at']:%Y-%m-%d}." if pr
              else "No periodic review is on record."),
           [atr and f"Audit Trail Review v{atr['version']}", pr and f"Periodic Review v{pr['version']}"]),
    ]


class Answer(BaseModel):
    answer: str
    citations: list[str]
    found_in_records: bool


def ask(system_id, user: dict, question: str) -> dict:
    """Answer a free-text inspection question from the records only (one model call)."""
    from .review import _package_digest
    from .agents import GROUND_RULES
    standard = inspection(system_id, user["id"])
    with db.read() as conn:
        digest = _package_digest(conn, system_id)
    facts = "\n".join(f"Q: {a['question']}\nA: {a['answer']}\nRecords: {'; '.join(a['citations'])}" for a in standard)
    ans, meta = llm.generate("agent:inspection", GROUND_RULES, f"""RECORDS OF THE VALIDATION SYSTEM

{facts}

{digest}

INSPECTOR'S QUESTION: {question}

Answer in at most five sentences using ONLY the records above. `citations` lists the exact records the answer
rests on (document and version, test or requirement ID, deviation, signature, release). If the records do not
contain the answer, say so plainly, set found_in_records to false and leave citations empty. Never infer.""", Answer)
    with db.tx(user["id"], db.APP) as conn:
        db.audit(conn, "inspection_question", "system", system_id, {**meta, "question": question[:200]})
    return ans.model_dump()
