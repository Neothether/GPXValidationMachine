"""Package review, Part 11 / Annex 11 control mapping, summary report and change impact."""
import re
from typing import Literal

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import db, ingest, llm, runner
from .agents import GROUND_RULES

# Citations are fixed here so the model maps evidence to real provisions instead of recalling them.
CONTROLS = [
    ("21 CFR 11.10(a)", "Validation of the system to ensure accuracy, reliability and consistent intended performance"),
    ("21 CFR 11.10(b)", "Ability to generate accurate and complete copies of records for inspection"),
    ("21 CFR 11.10(c)", "Protection of records to enable accurate and ready retrieval throughout retention"),
    ("21 CFR 11.10(d)", "Limiting system access to authorized individuals"),
    ("21 CFR 11.10(e)", "Secure, computer-generated, time-stamped audit trails of create/modify/delete actions"),
    ("21 CFR 11.10(f)", "Operational system checks to enforce permitted sequencing of steps"),
    ("21 CFR 11.10(g)", "Authority checks so only authorized individuals can use the system, sign or alter records"),
    ("21 CFR 11.10(h)", "Device checks to determine the validity of the source of data input"),
    ("21 CFR 11.10(k)", "Controls over system documentation, including revision and change control"),
    ("21 CFR 11.50", "Signature manifestations: printed name, date and time, and meaning of the signature"),
    ("21 CFR 11.70", "Signatures linked to their records so they cannot be excised, copied or transferred"),
    ("21 CFR 11.300", "Controls for identification codes and passwords"),
    ("EU Annex 11 §5", "Built-in checks for correct and secure entry and processing of data exchanged with other systems"),
    ("EU Annex 11 §6", "Accuracy checks for critical data entered manually"),
    ("EU Annex 11 §7", "Data secured against damage; regular backups; integrity and accuracy of backups checked"),
    ("EU Annex 11 §9", "Audit trail of GMP-relevant changes and deletions, with the reason documented"),
    ("EU Annex 11 §10", "Changes made in a controlled manner under a defined procedure"),
    ("EU Annex 11 §12", "Physical and logical controls restricting access to authorised persons"),
    ("EU Annex 11 §14", "Electronic signatures: same impact as handwritten, permanently linked, time and date"),
]


class Finding(BaseModel):
    severity: Literal["critical", "major", "minor"]
    document: str
    location: str
    finding: str
    recommended_action: str


class ControlStatus(BaseModel):
    rule: str
    status: Literal["met", "partial", "gap", "not_applicable"]
    evidence: str
    rationale: str


class Review(BaseModel):
    findings: list[Finding]
    controls: list[ControlStatus]


def _deterministic_findings(conn, system_id) -> list[Finding]:
    """Checks that need no judgement: chronology, signatures, evidence integrity, trace gaps."""
    out = []
    for r in conn.execute(
            """select a.kind, a.version, e.id, e.started_at, min(s.signed_at) as approved_at
                 from executions e join artifacts a on a.id = e.artifact_id
                 left join signatures s on s.artifact_id = a.id and s.meaning = 'approved'
                where e.system_id = %s group by a.kind, a.version, e.id, e.started_at
               having min(s.signed_at) is null or min(s.signed_at) >= e.started_at""", (system_id,)):
        out.append(Finding(severity="critical", document=f"{r['kind']} v{r['version']}", location=f"execution {r['id']}",
                           finding="Protocol was executed before it was approved.",
                           recommended_action="Raise a deviation; re-execute under an approved protocol."))
    early: dict[str, list[str]] = {}
    for r in conn.execute(
            """select p.kind as protocol, p.version, s.kind as spec,
                      (select min(signed_at) from signatures where artifact_id = p.id and meaning = 'approved') as protocol_approved,
                      (select min(signed_at) from signatures where artifact_id = s.id and meaning = 'approved') as spec_approved
                 from v_current_artifacts p join v_current_artifacts s on s.system_id = p.system_id
                where p.system_id = %s and p.kind in ('IQ','OQ','PQ') and s.kind in ('URS','FS','DS','RA')""", (system_id,)):
        if r["protocol_approved"] and (r["spec_approved"] is None or r["spec_approved"] > r["protocol_approved"]):
            early.setdefault(f"{r['protocol']} v{r['version']}", []).append(r["spec"])
    for doc, specs in early.items():
        out.append(Finding(severity="major", document=doc, location="signatures",
                           finding=f"Protocol was approved before the documents it verifies against were approved: {', '.join(sorted(specs))}.",
                           recommended_action="Confirm the protocol still matches the approved specifications and record the review."))
    for g in conn.execute("select * from v_gaps where system_id = %s", (system_id,)):
        sev = "major" if g["gap"] in ("untested_requirement", "failing_test", "not_executed") else "minor"
        out.append(Finding(severity=sev, document="Traceability matrix", location=g["ref"],
                           finding=f"{g['gap'].replace('_', ' ')}: {g['detail']}",
                           recommended_action="Close the gap before the summary report is approved."))
    for e in conn.execute("select id from executions where system_id = %s", (system_id,)).fetchall():
        for v in runner.verify_evidence(e["id"]):
            if not v["intact"]:
                out.append(Finding(severity="critical", document="Executed protocol", location=v["path"],
                                   finding="Evidence file is missing or its hash no longer matches the recorded hash.",
                                   recommended_action="Investigate as a data-integrity incident; re-execute the step."))
    chain = conn.execute("select * from vm_verify_audit_chain()").fetchone()
    if not chain["ok"]:
        out.append(Finding(severity="critical", document="Audit trail", location=f"event {chain['first_bad_id']}",
                           finding="Audit-trail hash chain is broken: an entry was altered or removed.",
                           recommended_action="Investigate as a data-integrity incident."))
    return out


def _package_digest(conn, system_id) -> str:
    parts = []
    for a in conn.execute("select * from v_current_artifacts where system_id = %s order by created_at", (system_id,)):
        sigs = conn.execute("select signer_name, meaning, signed_at from signatures where artifact_id = %s", (a["id"],)).fetchall()
        parts.append(f"\n===== {a['kind']} v{a['version']} [{a['status']}] author={a['author_id']} created={a['created_at']}\n"
                     f"signatures: {[(s['signer_name'], s['meaning'], str(s['signed_at'])) for s in sigs]}\n{a['content']}")
    for r in conn.execute(
            """select tc.test_id, sr.outcome, sr.recorded_at, sr.actual from step_results sr
                 join test_cases tc on tc.id = sr.test_case_id where tc.system_id = %s order by sr.recorded_at""", (system_id,)):
        parts.append(f"\n----- RESULT {r['test_id']} outcome={r['outcome']} at={r['recorded_at']}\n{r['actual'][-1200:]}")
    for d in conn.execute("select * from deviations where system_id = %s", (system_id,)):
        parts.append(f"\n----- DEVIATION status={d['status']} {d['description']} resolution={d['resolution']}")
    return "".join(parts)


def run_review(system_id, external_package: str | None = None) -> dict:
    """Review this system's package (or an externally supplied one) and map Part 11 / Annex 11 controls."""
    actor = "agent:reviewer"
    with db.read() as conn:
        s = conn.execute("select * from systems where id = %s", (system_id,)).fetchone()
        det = [] if external_package else _deterministic_findings(conn, system_id)
        digest = external_package or _package_digest(conn, system_id)
    catalog = "\n".join(f"- {rule}: {text}" for rule, text in CONTROLS)
    rev, meta = llm.generate(actor, GROUND_RULES, f"""INTENDED USE:
{s['intended_use']}

TARGET REPOSITORY (line-numbered):
{ingest.bundle(s['repo_path'])}

VALIDATION PACKAGE UNDER REVIEW:
{digest}

CONTROL CATALOG (use these `rule` strings exactly):
{catalog}

TASK 1 - findings: review the package as a QA reviewer would. Look for: results that contradict their
evidence (a 'pass' whose log shows a failure), steps executed before approval, missing or self-approved
signatures, dates out of order, requirements that are vague or untestable, risk ratings that contradict the
intended use, tests that do not actually verify their stated requirement, documents that disagree with each
other. `document` and `location` must point at the exact document and test/requirement ID. Report only
problems you can point to; return an empty list if there are none.

TASK 2 - controls: for EVERY rule in the catalog, state whether the TARGET system implements it.
`evidence` must be a path:line citation from the TARGET REPOSITORY, or 'none found'. The validation
documents, signatures and change records in the package belong to the validation tool, not to the target:
never cite them as evidence that the target implements a control. Status 'met' requires
code evidence; use 'gap' when the intended use needs the control and nothing implements it;
'not_applicable' only with a rationale tied to the intended use.""", Review)

    findings = det + rev.findings
    with db.tx(actor, db.AGENT) as conn:
        db.audit(conn, "llm_call", "system", system_id, meta)
        conn.execute("delete from findings where system_id = %s", (system_id,))
        conn.execute("delete from controls where system_id = %s", (system_id,))
        for f in findings:
            conn.execute(
                """insert into findings (system_id, severity, document, location, finding, recommended_action, found_by)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (system_id, f.severity, f.document, f.location, f.finding, f.recommended_action, actor))
        mapped = 0
        for rule, text in CONTROLS:     # the model may echo the rule with its description appended
            c = next((c for c in rev.controls if c.rule.strip() == rule or c.rule.strip().startswith(rule + ":")
                      or c.rule.strip().startswith(rule + " ")), None)
            if c:
                mapped += 1
                conn.execute(
                    "insert into controls (system_id, control, rule, evidence, status, rationale) values (%s, %s, %s, %s, %s, %s)",
                    (system_id, text, rule, c.evidence, c.status, c.rationale))
        db.audit(conn, "review_completed", "system", system_id,
                 {"findings": len(findings), "controls_returned": len(rev.controls), "controls_mapped": mapped})
    return {"findings": len(findings), "controls": mapped}


# --------------------------------------------------------------------------- Validation Summary Report
def draft_vsr(system_id) -> dict:
    """Built from records only, with no model call, so it cannot state anything the evidence does not show."""
    actor = "agent:vsr"
    with db.read() as conn:
        s = conn.execute("select * from systems where id = %s", (system_id,)).fetchone()
        arts = conn.execute("select * from v_current_artifacts where system_id = %s and kind <> 'VSR' order by created_at", (system_id,)).fetchall()
        trace = conn.execute("select * from v_trace where system_id = %s order by level, req_id, test_id", (system_id,)).fetchall()
        devs = conn.execute("select d.*, tc.test_id from deviations d join test_cases tc on tc.id = d.test_case_id "
                            "where d.system_id = %s order by d.opened_at", (system_id,)).fetchall()
        gaps = conn.execute("select * from v_gaps where system_id = %s and gap <> 'unapproved_document'", (system_id,)).fetchall()
        execs = conn.execute("select * from executions where system_id = %s order by started_at", (system_id,)).fetchall()
    tested = [t for t in trace if t["test_id"]]
    passed = [t for t in tested if t["outcome"] == "pass"]
    open_devs = [d for d in devs if d["status"] == "open"]
    unapproved = [a for a in arts if a["status"] != "approved"]
    supports = not gaps and not open_devs and not unapproved and tested
    lines = [f"# Validation Summary Report: {s['name']}", "",
             "**DRAFT. This report has no standing until approved and e-signed by Quality Assurance and the System Owner.**", "",
             "## 1. System", f"- GxP impact: {s['gxp_impact']}", f"- GAMP 5 category: {s['gamp_category']}",
             f"- Intended use: {s['intended_use']}", "",
             "## 2. Deliverables", "| Document | Version | Status | Author |", "|---|---|---|---|",
             *[f"| {a['kind']} | {a['version']} | {a['status']} | {a['author_id']} |" for a in arts], "",
             "## 3. Test execution", f"- Executions: {len(execs)}",
             f"- Requirement/test pairs with a passing latest result: {len(passed)} of {len(tested)}",
             *[f"- Execution `{e['id']}` at {e['started_at']:%Y-%m-%d %H:%M:%S} UTC, software fingerprint `{e['target_fingerprint'][:16]}`"
               for e in execs], "",
             "## 4. Deviations", "| Test | Status | Description | Resolution |", "|---|---|---|---|",
             *[f"| {d['test_id']} | {d['status']} | {d['description']} | {d['resolution'] or ''} |" for d in devs], "",
             "## 5. Open gaps", *([f"- {g['gap']}: {g['ref']}" for g in gaps] or ["- None"]), "",
             "## 6. Traceability", "| Requirement | Risk | Test | Result |", "|---|---|---|---|",
             *[f"| {t['req_id']} | {t['risk_rating'] or ''} | {t['test_id'] or 'NONE'} | {t['outcome'] or 'not executed'} |" for t in trace], "",
             "## 7. Conclusion (draft, for QA decision)",
             ("The records support a conclusion that the system is fit for its intended use: all traced tests pass, "
              "all deviations are closed and all deliverables are approved." if supports else
              f"The records do NOT yet support release: {len(gaps)} open gap(s), {len(open_devs)} open deviation(s), "
              f"{len(unapproved)} unapproved deliverable(s)."), "",
             "## 8. Approval", "| Role | Name | Meaning | Date/time |", "|---|---|---|---|",
             "| Quality Assurance | _pending e-signature_ | Approved | |", "| System Owner | _pending e-signature_ | Approved | |"]
    with db.tx(actor, db.AGENT) as conn:
        return db.create_artifact(conn, system_id, "VSR", f"Validation Summary Report: {s['name']}", "\n".join(lines), actor)


# --------------------------------------------------------------------------- change impact
class Impact(BaseModel):
    req_id: str
    reason: str


class ChangeImpact(BaseModel):
    summary: str
    impacted: list[Impact]
    not_impacted_rationale: str
    risk_of_change: Literal["low", "medium", "high"]


def _diff_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    """File -> pre-change line ranges touched by a unified diff."""
    ranges, current = {}, None
    for line in diff.splitlines():
        if line.startswith("--- "):
            current = re.sub(r"^(a/)", "", line[4:].split("\t")[0].strip())
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? ", line)
        if m and current:
            start, n = int(m.group(1)), int(m.group(2) or 1)
            ranges.setdefault(current, []).append((start, start + max(n, 1) - 1))
    return ranges


def _ref_hit(source_ref: str, ranges) -> bool:
    for m in re.finditer(r"([\w./\\-]+):(\d+)(?:-(\d+))?", source_ref or ""):
        path, a, b = m.group(1).replace("\\", "/"), int(m.group(2)), int(m.group(3) or m.group(2))
        for f, spans in ranges.items():
            if f.endswith(path) or path.endswith(f):
                if any(a <= hi and b >= lo for lo, hi in spans):
                    return True
    return False


def assess_change(change_id) -> dict:
    """Impacted requirements = cited lines touched by the diff (exact) + semantic impact (agent).
    Regression scope = every test traced to an impacted requirement, its parent or its children."""
    actor = "agent:change_impact"
    with db.read() as conn:
        ch = conn.execute("select c.*, s.name, s.intended_use from changes c join systems s on s.id = c.system_id where c.id = %s", (change_id,)).fetchone()
        reqs = conn.execute(
            """select r.* from requirements r join v_current_artifacts a on a.id = r.artifact_id
                where r.system_id = %s order by r.level, r.req_id""", (ch["system_id"],)).fetchall()
    ranges = _diff_ranges(ch["diff"])
    by_lines = {r["req_id"] for r in reqs if _ref_hit(r["source_ref"], ranges)}
    listing = "\n".join(f"{r['req_id']} [{r['level']}]: {r['statement']} [{r['source_ref']}]" for r in reqs)
    ci, meta = llm.generate(actor, GROUND_RULES, f"""INTENDED USE:
{ch['intended_use']}

CHANGE ({ch['kind']}): {ch['description']}

{ch['diff']}

REQUIREMENTS:
{listing}

Requirements whose cited source lines are touched by this diff: {sorted(by_lines) or 'none'}

TASK: Change impact assessment.
- impacted: every requirement whose behaviour this change could alter, including ones not on the list
  above (a changed constant, calculation or shared helper affects every requirement that depends on it).
  Give the reason for each. Read vendor release notes sceptically: a 'performance' or 'refactor' note can
  hide a behavioural change.
- not_impacted_rationale: why the remaining requirements need no retest.
- risk_of_change: overall, given the intended use.""", ChangeImpact)

    known = {r["req_id"]: r for r in reqs}
    reasons = {i.req_id: i.reason for i in ci.impacted if i.req_id in known}
    for rid in by_lines:
        reasons.setdefault(rid, "Cited source lines are modified by the diff.")
    ids = {known[rid]["id"] for rid in reasons}
    ids |= {known[rid]["parent_id"] for rid in reasons if known[rid]["parent_id"]}
    ids |= {r["id"] for r in reqs if r["parent_id"] in ids}
    with db.read() as conn:
        tests = conn.execute(
            """select distinct tc.id, tc.test_id, tc.title, tc.artifact_id, a.kind from test_requirements tr
                 join test_cases tc on tc.id = tr.test_case_id join v_current_artifacts a on a.id = tc.artifact_id
                where tr.requirement_id = any(%s) order by tc.test_id""", (list(ids),)).fetchall()
        total = conn.execute("select count(*) as n from test_cases tc join v_current_artifacts a on a.id = tc.artifact_id "
                             "where tc.system_id = %s", (ch["system_id"],)).fetchone()["n"]
    impact = {"summary": ci.summary, "risk_of_change": ci.risk_of_change,
              "impacted_requirements": [{"req_id": k, "reason": v} for k, v in sorted(reasons.items())],
              "regression_scope": [{"test_case_id": str(t["id"]), "test_id": t["test_id"], "title": t["title"],
                                    "artifact_id": str(t["artifact_id"]), "protocol": t["kind"]} for t in tests],
              "total_tests": total}
    content = "\n".join([
        f"# Change Record: {ch['description']}", "", f"**Type:** {ch['kind']}   **Risk of change:** {ci.risk_of_change}", "",
        "## Summary", ci.summary, "", "## Impacted requirements", "| Requirement | Reason |", "|---|---|",
        *[f"| {i['req_id']} | {i['reason']} |" for i in impact["impacted_requirements"]], "",
        f"## Regression scope ({len(tests)} of {total} tests)", *[f"- {t['test_id']}: {t['title']}" for t in tests], "",
        "## Not impacted", ci.not_impacted_rationale, "", "## Change", "```diff", ch["diff"], "```"])
    with db.tx(actor, db.AGENT) as conn:
        db.audit(conn, "llm_call", "change", change_id, meta)
        art = db.create_artifact(conn, ch["system_id"], "CHANGE", f"Change Record: {ch['description']}", content, actor)
        conn.execute("update changes set impact = %s, rationale = %s, artifact_id = %s where id = %s",
                     (Jsonb(impact), ci.not_impacted_rationale, art["id"], change_id))
    return impact
