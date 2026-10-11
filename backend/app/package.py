"""The Validation Package: every deliverable, signature, result and trace row in one printable document."""
from datetime import datetime, timezone
from html import escape

import markdown

from . import db

CSS = """
body{font:14px/1.5 'Segoe UI',system-ui,sans-serif;color:#1a1a1a;max-width:1000px;margin:2rem auto;padding:0 1rem}
h1{font-size:1.5rem;border-bottom:2px solid #1a1a1a;padding-bottom:.3rem;margin-top:2.5rem}
h2{font-size:1.15rem;margin-top:1.6rem} table{border-collapse:collapse;width:100%;margin:.6rem 0;font-size:12.5px}
th,td{border:1px solid #bbb;padding:4px 7px;text-align:left;vertical-align:top} th{background:#f0f0f0}
code,pre{font:12px Consolas,monospace;background:#f6f6f6} pre{padding:.6rem;overflow-x:auto;white-space:pre-wrap}
.meta{color:#555;font-size:12px} .pass{color:#0a6b2d;font-weight:600} .fail,.error{color:#b00020;font-weight:600}
.sig{border:1px solid #1a1a1a;padding:.5rem .8rem;margin:.4rem 0} .draft{color:#b00020;font-weight:600}
.summary{border:2px solid #1a1a1a;padding:.4rem 1rem;margin:1rem 0} .noprint{float:right;padding:.4rem .8rem;cursor:pointer}
.doc{page-break-before:always} @media print{body{margin:0;max-width:none} .noprint{display:none}}
"""


def _table(headers, rows) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _e(v) -> str:
    return escape("" if v is None else str(v))


def render(system_id, snapshot_label: str | None = None) -> str:
    with db.read() as conn:
        s = conn.execute("select * from systems where id = %s", (system_id,)).fetchone()
        blockers = conn.execute("select * from vm_release_blockers(%s)", (system_id,)).fetchall()
        release = conn.execute(
            """select r.*, u.display_name from releases r join users u on u.id = r.released_by
                where r.system_id = %s order by r.released_at desc limit 1""", (system_id,)).fetchone()
        # current version of each deliverable, plus every dated record (changes, reviews) ever made
        arts = conn.execute(
            """select * from artifacts a where a.system_id = %s
                  and (a.id in (select id from v_current_artifacts) or a.kind in ('CHANGE','PR','ATR'))
                order by a.created_at""", (system_id,)).fetchall()
        trace = conn.execute("select * from v_trace where system_id = %s order by level desc, req_id, test_id", (system_id,)).fetchall()
        gaps = conn.execute("select * from v_gaps where system_id = %s order by gap, ref", (system_id,)).fetchall()
        results = conn.execute(
            """select tc.test_id, tc.title, sr.outcome, sr.recorded_at, sr.recorded_by, ev.path, ev.sha256,
                      e.target_fingerprint, e.environment, e.id as execution_id
                 from step_results sr join test_cases tc on tc.id = sr.test_case_id
                 join evidence ev on ev.id = sr.evidence_id join executions e on e.id = sr.execution_id
                where e.system_id = %s order by sr.recorded_at""", (system_id,)).fetchall()
        devs = conn.execute("select d.*, tc.test_id from deviations d join test_cases tc on tc.id = d.test_case_id "
                            "where d.system_id = %s order by opened_at", (system_id,)).fetchall()
        controls = conn.execute("select * from controls where system_id = %s order by rule", (system_id,)).fetchall()
        findings = conn.execute("select * from findings where system_id = %s order by severity", (system_id,)).fetchall()
        chain = conn.execute("select * from vm_verify_audit_chain()").fetchone()
        sigs = {a["id"]: conn.execute("select * from signatures where artifact_id = %s order by signed_at", (a["id"],)).fetchall()
                for a in arts}

    now = datetime.now(timezone.utc)
    tests_seen = {t["test_id"]: t["outcome"] for t in trace if t["test_id"]}
    status = ("VALIDATED" if s["status"] == "validated" else
              "NOT VALIDATED: this package is a draft until the Validation Summary Report is approved by QA and the System Owner")
    out = [f"<!doctype html><meta charset='utf-8'><title>Validation Package: {_e(s['name'])}</title><style>{CSS}</style>",
           "<button class='noprint' onclick='window.print()'>Print or save as PDF</button>",
           f"<h1>Validation Package: {_e(s['name'])}</h1>",
           f"<p class='meta'>{'Snapshot: ' + _e(snapshot_label) + '. ' if snapshot_label else ''}"
           f"Generated {now:%Y-%m-%d %H:%M:%S} UTC from system-of-record. "
           f"Audit trail: {chain['checked']} events, hash chain {'intact' if chain['ok'] else 'BROKEN'}.</p>",
           # One-page summary: the verdict, the numbers behind it, and what is still open.
           f"<div class='summary'><p class='{'pass' if s['status'] == 'validated' else 'draft'}'>Status: {status}</p>",
           (f"<p>Released {release['released_at']:%Y-%m-%d %H:%M} UTC by <b>{_e(release['display_name'])}</b> for software "
            f"fingerprint <code>{_e(release['fingerprint'][:16])}</code>.</p>" if release else "<p>No release on record.</p>"),
           f"<p>Software fingerprint at generation: <code>{_e((s['current_fingerprint'] or 'not recorded')[:16])}</code>"
           + (" <span class='fail'>(differs from the released version)</span>"
              if release and s["current_fingerprint"] and release["fingerprint"] != s["current_fingerprint"] else "") + "</p>",
           _table(["Requirements", "Tests", "Passing", "Failing", "Not executed", "Deviations open / closed / accepted", "Signatures"],
                  [[len({t["req_id"] for t in trace}), len(tests_seen), sum(1 for o in tests_seen.values() if o == "pass"),
                    sum(1 for o in tests_seen.values() if o not in (None, "pass")), sum(1 for o in tests_seen.values() if o is None),
                    " / ".join(str(sum(1 for d in devs if d["status"] == k)) for k in ("open", "resolved", "accepted")),
                    sum(len(v) for v in sigs.values())]]),
           ("<p><b>Open release blockers</b></p>" + _table(["Check", "Reference", "Detail"],
                                                           [[_e(b["code"].replace("_", " ")), _e(b["ref"]), _e(b["detail"])] for b in blockers])
            if blockers else "<p class='pass'>No release blockers.</p>"), "</div>",
           _table(["GxP impact", "GAMP category", "Rationale"], [[_e(s["gxp_impact"]), _e(s["gamp_category"]), _e(s["category_rationale"])]]),
           f"<h2>Intended use</h2><p>{_e(s['intended_use'])}</p>",
           "<h1>Traceability matrix</h1>",
           _table(["Requirement", "Statement", "Source", "Risk", "Test", "Protocol", "Result", "Executed (UTC)", "Evidence SHA-256"],
                  [[_e(t["req_id"]), _e(t["statement"]), f"<code>{_e(t['source_ref'])}</code>", _e(t["risk_rating"]),
                    _e(t["test_id"]) or "<span class='fail'>none</span>",
                    _e(f"{t['protocol_kind']} v{t['protocol_version']} ({t['protocol_status']})") if t["test_id"] else "",
                    f"<span class='{_e(t['outcome'])}'>{_e(t['outcome'] or 'not executed')}</span>",
                    _e(t["executed_at"] and f"{t['executed_at']:%Y-%m-%d %H:%M:%S}"), f"<code>{_e((t['evidence_sha256'] or '')[:16])}</code>"]
                   for t in trace]),
           "<h2>Gap report</h2>",
           _table(["Gap", "Reference", "Detail"], [[_e(g["gap"]), _e(g["ref"]), _e(g["detail"])] for g in gaps]) if gaps else "<p>No open gaps.</p>",
           "<h1>Executed tests and objective evidence</h1>",
           _table(["Test", "Result", "Recorded (UTC)", "By", "Software fingerprint", "Environment", "Evidence file", "SHA-256"],
                  [[_e(r["test_id"]), f"<span class='{_e(r['outcome'])}'>{_e(r['outcome'])}</span>", f"{r['recorded_at']:%Y-%m-%d %H:%M:%S}",
                    _e(r["recorded_by"]), f"<code>{_e(r['target_fingerprint'][:16])}</code>",
                    _e(("Manual step / " if r["environment"].get("manual") else f"Python {r['environment'].get('python')} / ")
                       + str(r["environment"].get("platform"))),
                    f"<code>{_e(r['path'])}</code>", f"<code>{_e(r['sha256'])}</code>"] for r in results]),
           "<h1>Deviations</h1>",
           _table(["Test", "Status", "Description", "Investigation", "Resolution", "Resolved by"],
                  [[_e(d["test_id"]), _e(d["status"]), _e(d["description"]), _e(d["investigation"]), _e(d["resolution"]),
                    _e(d["resolved_by"])] for d in devs]) if devs else "<p>No deviations.</p>",
           "<h1>21 CFR Part 11 / EU Annex 11 control map (target system)</h1>",
           _table(["Rule", "Control", "Status", "Evidence", "Rationale"],
                  [[_e(c["rule"]), _e(c["control"]), _e(c["status"]), f"<code>{_e(c['evidence'])}</code>", _e(c["rationale"])] for c in controls]),
           "<h1>Review findings</h1>",
           _table(["Severity", "Document", "Location", "Finding", "Recommended action"],
                  [[_e(f["severity"]), _e(f["document"]), _e(f["location"]), _e(f["finding"]), _e(f["recommended_action"])] for f in findings])]
    for a in arts:
        out.append(f"<div class='doc'><p class='meta'>{_e(a['kind'])} v{a['version']} | status: {_e(a['status'])} | "
                   f"author: {_e(a['author_id'])} | content SHA-256: <code>{_e(a['content_hash'])}</code></p>")
        out.append(markdown.markdown(a["content"], extensions=["tables", "fenced_code"]))
        for g in sigs[a["id"]]:
            out.append(f"<div class='sig'>Electronically signed by <b>{_e(g['signer_name'])}</b> | meaning: <b>{_e(g['meaning'])}</b> | "
                       f"{g['signed_at']:%Y-%m-%d %H:%M:%S} UTC | bound to content <code>{_e(g['content_hash'][:16])}</code></div>")
        if not sigs[a["id"]]:
            out.append("<p class='draft'>Not signed.</p>")
        out.append("</div>")
    return "".join(out)
