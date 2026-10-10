import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError, fmt, setToken } from "./api";

type Act = (label: string, fn: () => Promise<unknown>, done?: string) => Promise<void>;
type Banner = { kind: "ok" | "bad"; title: string; text: string } | null;
type P = { sid: string; rev: number; act: Act };

const TABS = ["Pipeline", "Traceability", "Execution", "Deviations", "Review", "Change control", "Audit trail"] as const;

function useLoad<T = any>(path: string | null, rev: number): T | undefined {
  const [data, setData] = useState<T>();
  useEffect(() => {
    let live = true;
    if (path) api<T>(path).then((d) => live && setData(d)).catch(() => live && setData(undefined));
    return () => {
      live = false;
    };
  }, [path, rev]);
  return data;
}

const tone = (v?: string | null) =>
  !v ? "" : ["approved", "pass", "validated", "met", "resolved", "low"].includes(v) ? "ok"
    : ["fail", "error", "open", "gap", "critical", "high", "major"].includes(v) ? "bad"
    : ["draft", "partial", "medium", "minor", "change_pending", "in_validation"].includes(v) ? "warn" : "";

const Chip = ({ v }: { v?: string | null }) => (v ? <span className={`chip ${tone(v)}`}>{v.replace(/_/g, " ")}</span> : null);

function Table({ cols, rows, empty = "Nothing yet." }: { cols: string[]; rows: ReactNode[][]; empty?: string }) {
  if (!rows.length) return <p className="muted">{empty}</p>;
  return (
    <div className="tablewrap">
      <table>
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

export default function App() {
  const [users, setUsers] = useState<any[]>([]);
  const [sessions, setSessions] = useState<Record<string, string>>({});
  const [user, setUser] = useState<any>(null);
  const [pick, setPick] = useState("");
  const [password, setPassword] = useState("");
  const [sid, setSid] = useState<string | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Pipeline");
  const [banner, setBanner] = useState<Banner>(null);
  const [busy, setBusy] = useState("");
  const [rev, setRev] = useState(0);

  useEffect(() => {
    api("/users").then((u) => {
      setUsers(u);
      setPick(u.find((x: any) => x.is_human)?.id ?? "");
    }).catch((e) => setBanner({ kind: "bad", title: "API unreachable", text: String(e.message) }));
  }, []);

  const act: Act = async (label, fn, done) => {
    setBusy(label);
    setBanner(null);
    try {
      await fn();
      if (done) setBanner({ kind: "ok", title: done, text: "" });
    } catch (e) {
      const enforced = e instanceof ApiError && e.enforced;
      setBanner({ kind: "bad", title: enforced ? "Rejected by the database" : "Could not complete", text: (e as Error).message });
    } finally {
      setBusy("");
      setRev((r) => r + 1);
    }
  };

  const signIn = () =>
    act("Signing in", async () => {
      let token = sessions[pick];
      if (!token) {
        const r = await api("/login", { user_id: pick, password });
        token = r.token;
        setSessions((s) => ({ ...s, [pick]: token }));
      }
      setToken(token);
      setUser(users.find((u) => u.id === pick));
      setPassword("");
    });

  return (
    <>
      <header className="top">
        <h1>The Validation Machine</h1>
        {sid && <button className="link" onClick={() => setSid(null)}>All systems</button>}
        <span className="spacer" />
        {busy && <span className="chip warn">{busy}...</span>}
        <span className="who">{user ? <>Acting as <b>{user.display_name}</b></> : "Not signed in"}</span>
        <select value={pick} onChange={(e) => setPick(e.target.value)}>
          {users.filter((u) => u.is_human).map((u) => <option key={u.id} value={u.id}>{u.id} ({u.role.replace("_", " ")})</option>)}
        </select>
        {!sessions[pick] && (
          <input type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && signIn()} />
        )}
        <button onClick={signIn} disabled={!pick || !!busy}>{sessions[pick] ? "Switch" : "Sign in"}</button>
      </header>
      <main>
        {banner && (
          <div className={`banner ${banner.kind}`}>
            <b>{banner.title}</b>
            {banner.text}
          </div>
        )}
        {!sid ? <Inventory rev={rev} act={act} open={setSid} signedIn={!!user} /> : (
          <>
            <SystemHeader sid={sid} rev={rev} act={act} />
            <nav className="tabs">
              {TABS.map((t) => <button key={t} className={t === tab ? "on" : ""} onClick={() => setTab(t)}>{t}</button>)}
            </nav>
            {tab === "Pipeline" && <Pipeline sid={sid} rev={rev} act={act} />}
            {tab === "Traceability" && <Trace sid={sid} rev={rev} act={act} />}
            {tab === "Execution" && <Executions sid={sid} rev={rev} act={act} />}
            {tab === "Deviations" && <Deviations sid={sid} rev={rev} act={act} />}
            {tab === "Review" && <Review sid={sid} rev={rev} act={act} />}
            {tab === "Change control" && <Changes sid={sid} rev={rev} act={act} />}
            {tab === "Audit trail" && <Audit rev={rev} act={act} />}
          </>
        )}
      </main>
    </>
  );
}

function Inventory({ rev, act, open, signedIn }: { rev: number; act: Act; open: (id: string) => void; signedIn: boolean }) {
  const systems = useLoad<any[]>("/systems", rev) ?? [];
  const [f, setF] = useState({ name: "", repo_path: "", intended_use: "", sop: "" });
  const set = (k: keyof typeof f) => (e: any) => setF({ ...f, [k]: e.target.value });
  const demo = (which: string) =>
    act("Loading demo", async () => {
      const d = await api("/demo");
      setF({ name: `${d.name} (${which})`, repo_path: d.repo_path, intended_use: d.intended_use[which], sop: d.sop });
    });
  return (
    <>
      <div className="panel">
        <h2>System inventory</h2>
        <Table
          cols={["System", "Status", "GxP impact", "GAMP", "Open gaps", "Open deviations", "Last executed", ""]}
          rows={systems.map((s) => [s.name, <Chip v={s.status} />, s.gxp_impact, s.gamp_category, s.open_gaps, s.open_deviations,
            fmt(s.last_executed), <button onClick={() => open(s.id)}>Open</button>])}
          empty="No systems registered yet."
        />
      </div>
      <div className="panel">
        <h2>Register a system for validation</h2>
        <div className="row">
          <span className="muted small">Demo target:</span>
          <button onClick={() => demo("submission")}>CDM Query Bot, data submitted to FDA</button>
          <button onClick={() => demo("internal")}>CDM Query Bot, internal dashboard</button>
        </div>
        <div className="grid2">
          <div><label>System name</label><input style={{ width: "100%" }} value={f.name} onChange={set("name")} /></div>
          <div><label>Repository path</label><input style={{ width: "100%" }} value={f.repo_path} onChange={set("repo_path")} /></div>
          <div><label>Intended-use statement</label><textarea value={f.intended_use} onChange={set("intended_use")} /></div>
          <div><label>Company CSV SOP</label><textarea value={f.sop} onChange={set("sop")} /></div>
        </div>
        <p>
          <button className="primary" disabled={!signedIn || !f.name || !f.repo_path || !f.intended_use}
                  onClick={() => act("Registering", async () => open((await api("/systems", f)).id))}>
            Register
          </button>
          {!signedIn && <span className="muted small"> Sign in first.</span>}
        </p>
      </div>
    </>
  );
}

function SystemHeader({ sid, rev, act }: P) {
  const s = useLoad<any>(`/systems/${sid}`, rev);
  if (!s) return null;
  return (
    <div className="panel">
      <div className="row">
        <h2 style={{ margin: 0 }}>{s.name}</h2>
        <Chip v={s.status} />
        {s.gxp_impact && <span className="chip">GxP impact: {s.gxp_impact}</span>}
        {s.gamp_category && <span className="chip">GAMP category {s.gamp_category}</span>}
        <span style={{ flex: 1 }} />
        <a href={`/api/systems/${sid}/package`} target="_blank" rel="noreferrer">Validation package</a>
        <button onClick={() => act("Updating status", () => api(`/systems/${sid}/status/validated`, {}), "System released as validated")}>
          Release as validated
        </button>
      </div>
      <p className="muted small" style={{ marginBottom: 0 }}>{s.intended_use}</p>
    </div>
  );
}

const STAGES = [
  ["plan", "Plan and categorise", "Validation Plan, GxP impact, GAMP category", ["VP"]],
  ["specs", "Specify", "URS, FS and DS with source links", ["URS", "FS", "DS"]],
  ["risk", "Assess risk", "Function-level risk with rationale", ["RA"]],
  ["protocols", "Author tests", "IQ, OQ and PQ protocols", ["IQ", "OQ", "PQ"]],
  ["vsr", "Summarise", "Validation Summary Report from records", ["VSR"]],
] as const;

function Pipeline({ sid, rev, act }: P) {
  const s = useLoad<any>(`/systems/${sid}`, rev);
  const [openId, setOpenId] = useState<string | null>(null);
  const arts: any[] = (s?.artifacts ?? []).filter((a: any) => a.current);
  return (
    <>
      <div className="panel">
        <h2>Lifecycle</h2>
        <div className="stages">
          {STAGES.map(([key, title, sub, kinds], i) => (
            <div className="stage" key={key}>
              <div className="n">Step {i + 1}</div>
              <b>{title}</b>
              <div className="muted small">{sub}</div>
              <div className="row" style={{ margin: "8px 0" }}>
                {kinds.map((k) => {
                  const a = arts.find((x) => x.kind === k);
                  return <span key={k} className={`chip ${tone(a?.status)}`}>{k}{a ? ` v${a.version} ${a.status}` : ""}</span>;
                })}
              </div>
              <button onClick={() => act(`Drafting (${title})`, () => api(`/systems/${sid}/run/${key}`, {}))}>Draft with agent</button>
            </div>
          ))}
        </div>
      </div>
      <div className="panel">
        <h2>Deliverables</h2>
        <Table
          cols={["Document", "Version", "Status", "Author", "Created", ""]}
          rows={arts.map((a) => [a.title, a.version, <Chip v={a.status} />, <code>{a.author_id}</code>, fmt(a.created_at),
            <button onClick={() => setOpenId(a.id === openId ? null : a.id)}>{a.id === openId ? "Close" : "Review"}</button>])}
          empty="Run step 1 to draft the first deliverable."
        />
      </div>
      {openId && <Document aid={openId} rev={rev} act={act} />}
    </>
  );
}

function Document({ aid, rev, act }: { aid: string; rev: number; act: Act }) {
  const a = useLoad<any>(`/artifacts/${aid}`, rev);
  const [password, setPassword] = useState("");
  const [meaning, setMeaning] = useState("approved");
  if (!a) return null;
  const html = DOMPurify.sanitize(marked.parse(a.content, { async: false }) as string);
  const executable = ["IQ", "OQ", "PQ"].includes(a.kind);
  return (
    <div className="panel">
      <div className="row">
        <h2 style={{ margin: 0 }}>{a.kind} v{a.version}</h2>
        <Chip v={a.status} />
        <span className="muted small">author <code>{a.author_id}</code> | content SHA-256 <code>{a.content_hash.slice(0, 16)}</code></span>
      </div>
      <div className="doc" dangerouslySetInnerHTML={{ __html: html }} />
      <h3>Electronic signatures</h3>
      {a.signatures.length === 0 && <p className="muted">Not signed.</p>}
      {a.signatures.map((g: any) => (
        <div className="sig" key={g.id}>
          Signed by <b>{g.signer_name}</b> | meaning: <b>{g.meaning}</b> | {fmt(g.signed_at)} | bound to content <code>{g.content_hash.slice(0, 16)}</code>
        </div>
      ))}
      <div className="row" style={{ marginTop: 10 }}>
        <select value={meaning} onChange={(e) => setMeaning(e.target.value)}>
          <option value="approved">Approved</option>
          <option value="reviewed">Reviewed</option>
        </select>
        <input type="password" placeholder="Re-enter password to sign" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button className="primary" disabled={!password}
                onClick={() => act("Signing", async () => { await api(`/artifacts/${aid}/sign`, { password, meaning }); setPassword(""); }, "Signature applied")}>
          Sign
        </button>
        {executable && (
          <button onClick={() => act("Executing protocol", () => api(`/artifacts/${aid}/execute`, {}), "Protocol executed; see Execution")}>
            Execute protocol
          </button>
        )}
      </div>
    </div>
  );
}

function Trace({ sid, rev }: P) {
  const rows = useLoad<any[]>(`/systems/${sid}/trace`, rev) ?? [];
  const gaps = useLoad<any[]>(`/systems/${sid}/gaps`, rev) ?? [];
  const risks = useLoad<any[]>(`/systems/${sid}/risks`, rev) ?? [];
  return (
    <>
      <div className="panel">
        <h2>Gap report <span className={`chip ${gaps.length ? "bad" : "ok"}`}>{gaps.length} open</span></h2>
        <Table cols={["Gap", "Reference", "Detail"]} rows={gaps.map((g) => [<Chip v={g.gap} />, g.ref, g.detail])} empty="No gaps." />
      </div>
      <div className="panel">
        <h2>Traceability matrix</h2>
        <Table
          cols={["Requirement", "Statement", "Source", "Traces to", "Risk", "Test", "Protocol", "Result", "Executed", "Evidence"]}
          rows={rows.map((t) => [<b>{t.req_id}</b>, t.statement, <code>{t.source_ref}</code>, t.parent_req_id, <Chip v={t.risk_rating} />,
            t.test_id ?? <span className="chip bad">none</span>,
            t.test_id && <>{t.protocol_kind} v{t.protocol_version} <Chip v={t.protocol_status} /></>,
            t.test_id && <Chip v={t.outcome ?? "not_executed"} />, fmt(t.executed_at), <code>{t.evidence_sha256?.slice(0, 12)}</code>])}
          empty="Draft the specifications to populate the matrix."
        />
      </div>
      <div className="panel">
        <h2>Risk assessment</h2>
        <Table
          cols={["Function", "Severity", "Probability", "Detectability", "Rating", "Test depth", "Rationale", "Basis"]}
          rows={risks.map((k) => [<b>{k.req_id}</b>, k.severity, k.probability, k.detectability, <Chip v={k.rating} />, k.test_depth, k.rationale, k.citation])}
        />
      </div>
    </>
  );
}

function Executions({ sid, rev, act }: P) {
  const execs = useLoad<any[]>(`/systems/${sid}/executions`, rev) ?? [];
  const [log, setLog] = useState<string | null>(null);
  const [verified, setVerified] = useState<Record<string, string>>({});
  if (!execs.length) return <div className="panel"><p className="muted">No executions yet. Approve a protocol, then execute it from the Pipeline tab.</p></div>;
  return (
    <>
      {execs.map((e) => (
        <div className="panel" key={e.id}>
          <div className="row">
            <h2 style={{ margin: 0 }}>{e.kind} v{e.version} {e.retest_of && <span className="chip">retest</span>}</h2>
            <span className="muted small">
              {fmt(e.started_at)} | by <code>{e.runner_id}</code> | software <code>{e.target_fingerprint.slice(0, 16)}</code> | Python {e.environment.python} on {e.environment.platform}
            </span>
            <span style={{ flex: 1 }} />
            {verified[e.id] && <span className={`chip ${verified[e.id] === "intact" ? "ok" : "bad"}`}>evidence {verified[e.id]}</span>}
            <button onClick={() => act("Verifying evidence", async () => {
              const v: any[] = await api(`/executions/${e.id}/verify`);
              setVerified((s) => ({ ...s, [e.id]: v.every((x) => x.intact) ? "intact" : "ALTERED" }));
            })}>Verify evidence hashes</button>
          </div>
          <Table
            cols={["Test", "Title", "Result", "Recorded", "Evidence SHA-256", ""]}
            rows={e.results.map((r: any) => [<b>{r.test_id}</b>, r.title, <Chip v={r.outcome} />, fmt(r.recorded_at), <code>{r.sha256.slice(0, 16)}</code>,
              <button className="link" onClick={() => setLog(log === r.id ? null : r.id)}>{log === r.id ? "Hide log" : "Show log"}</button>])}
          />
          {e.results.filter((r: any) => r.id === log).map((r: any) => <pre key={r.id}>{r.actual}</pre>)}
        </div>
      ))}
    </>
  );
}

function Deviations({ sid, rev, act }: P) {
  const devs = useLoad<any[]>(`/systems/${sid}/deviations`, rev) ?? [];
  const [retests, setRetests] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  if (!devs.length) return <div className="panel"><p className="muted">No deviations.</p></div>;
  return (
    <>
      {devs.map((d) => {
        const fix = d.proposed_fix ? JSON.parse(d.proposed_fix) : null;
        return (
          <div className="panel" key={d.id}>
            <div className="row"><h2 style={{ margin: 0 }}>{d.test_id}: {d.title}</h2><Chip v={d.status} /><span className="muted small">opened {fmt(d.opened_at)}</span></div>
            <p>{d.description}</p>
            <pre>{d.actual.slice(-1500)}</pre>
            {d.investigation && <><h3>Investigation (drafted by agent)</h3><p style={{ whiteSpace: "pre-wrap" }}>{d.investigation}</p></>}
            {fix?.original_snippet && (
              <>
                <h3>Proposed fix in <code>{fix.file}</code></h3>
                <div className="grid2"><pre>{fix.original_snippet}</pre><pre>{fix.replacement_snippet}</pre></div>
              </>
            )}
            {fix?.is_test_defect && <p className="chip warn">Agent believes the test, not the application, is defective. Revise the protocol under a new version.</p>}
            {d.status === "open" ? (
              <>
                <div className="row">
                  <button onClick={() => act("Investigating", () => api(`/deviations/${d.id}/investigate`, {}))}>Investigate with agent</button>
                  <button disabled={!fix?.original_snippet} onClick={() => act("Applying fix", () => api(`/deviations/${d.id}/apply-fix`, {}), "Fix applied to the target")}>Accept and apply fix</button>
                  <button onClick={() => act("Retesting", async () => {
                    const r = await api(`/deviations/${d.id}/retest`, {});
                    setRetests((s) => ({ ...s, [d.id]: r.execution_id }));
                  })}>Retest</button>
                  {retests[d.id] && <span className="muted small">retest execution <code>{retests[d.id].slice(0, 8)}</code></span>}
                </div>
                <label>Resolution</label>
                <div className="row">
                  <input style={{ flex: 1 }} value={notes[d.id] ?? ""} onChange={(e) => setNotes({ ...notes, [d.id]: e.target.value })} />
                  <button className="primary" disabled={!notes[d.id] || !retests[d.id]}
                          onClick={() => act("Closing deviation", () => api(`/deviations/${d.id}/resolve`, { resolution: notes[d.id], retest_execution_id: retests[d.id] }), "Deviation closed")}>
                    Close deviation
                  </button>
                </div>
              </>
            ) : <p><b>Resolution:</b> {d.resolution} <span className="muted small">by {d.resolved_by}, {fmt(d.resolved_at)}</span></p>}
          </div>
        );
      })}
    </>
  );
}

function Review({ sid, rev, act }: P) {
  const data = useLoad<any>(`/systems/${sid}/findings`, rev);
  const [ext, setExt] = useState("");
  return (
    <>
      <div className="panel">
        <div className="row">
          <h2 style={{ margin: 0 }}>Package review</h2>
          <button className="primary" onClick={() => act("Reviewing package", () => api(`/systems/${sid}/run/review`, {}))}>Review this package</button>
        </div>
        <h3>Findings</h3>
        <Table cols={["Severity", "Document", "Location", "Finding", "Recommended action"]}
               rows={(data?.findings ?? []).map((f: any) => [<Chip v={f.severity} />, f.document, f.location, f.finding, f.recommended_action])}
               empty="No findings recorded. Run the review." />
      </div>
      <div className="panel">
        <h2>21 CFR Part 11 / EU Annex 11 control map (target system)</h2>
        <Table cols={["Rule", "Control", "Status", "Evidence", "Rationale"]}
               rows={(data?.controls ?? []).map((c: any) => [<b>{c.rule}</b>, c.control, <Chip v={c.status} />, <code>{c.evidence}</code>, c.rationale])} />
      </div>
      <div className="panel">
        <h2>Audit-trail anomalies (target system)</h2>
        <Table cols={["Anomaly", "Actor", "When", "Detail"]}
               rows={(data?.audit_anomalies ?? []).map((a: any) => [<Chip v={a.anomaly} />, a.actor, fmt(a.at), a.detail])}
               empty="No target audit trail loaded, or no anomalies." />
      </div>
      <div className="panel">
        <h2>Review an external validation package</h2>
        <textarea placeholder="Paste a vendor or legacy validation package" value={ext} onChange={(e) => setExt(e.target.value)} />
        <p><button disabled={!ext} onClick={() => act("Reviewing external package", () => api(`/systems/${sid}/review-external`, { text: ext }))}>Review pasted package</button></p>
      </div>
    </>
  );
}

function Changes({ sid, rev, act }: P) {
  const changes = useLoad<any[]>(`/systems/${sid}/changes`, rev) ?? [];
  const [f, setF] = useState({ kind: "code_diff", description: "", diff: "" });
  const demo = () => act("Loading demo change", async () => {
    const d = await api("/demo");
    setF({ kind: "code_diff", description: "Refactor: tidy configuration constants", diff: d.changes["01_threshold_refactor"] });
  });
  return (
    <>
      <div className="panel">
        <h2>Assess a change</h2>
        <div className="row">
          <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
            <option value="code_diff">Code diff</option>
            <option value="config">Configuration change</option>
            <option value="vendor_release">Vendor release notes</option>
          </select>
          <input style={{ flex: 1 }} placeholder="Description" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
          <button onClick={demo}>Load demo change</button>
        </div>
        <label>Diff or release notes</label>
        <textarea style={{ minHeight: 140 }} value={f.diff} onChange={(e) => setF({ ...f, diff: e.target.value })} />
        <p><button className="primary" disabled={!f.description || !f.diff}
                   onClick={() => act("Assessing impact", () => api(`/systems/${sid}/changes`, f))}>Assess impact</button></p>
      </div>
      {changes.map((c) => (
        <div className="panel" key={c.id}>
          <div className="row">
            <h2 style={{ margin: 0 }}>{c.description}</h2>
            <Chip v={c.impact?.risk_of_change} />
            <span className="muted small">{fmt(c.created_at)}</span>
            <span style={{ flex: 1 }} />
            <button disabled={!c.impact?.regression_scope?.length}
                    onClick={() => act("Running regression", () => api(`/changes/${c.id}/regress`, {}), "Regression executed; see Execution")}>
              Run regression scope
            </button>
          </div>
          <p>{c.impact?.summary}</p>
          <h3>Impacted requirements</h3>
          <Table cols={["Requirement", "Reason"]} rows={(c.impact?.impacted_requirements ?? []).map((i: any) => [<b>{i.req_id}</b>, i.reason])} empty="None." />
          <h3>Regression scope: {c.impact?.regression_scope?.length ?? 0} of {c.impact?.total_tests ?? 0} tests</h3>
          <Table cols={["Test", "Protocol", "Title"]} rows={(c.impact?.regression_scope ?? []).map((t: any) => [<b>{t.test_id}</b>, t.protocol, t.title])} empty="No retest required." />
          <h3>Why the rest is not retested</h3>
          <p>{c.rationale}</p>
        </div>
      ))}
    </>
  );
}

function Audit({ rev, act }: { rev: number; act: Act }) {
  const events = useLoad<any[]>("/audit?limit=300", rev) ?? [];
  const [chain, setChain] = useState<any>(null);
  return (
    <div className="panel">
      <div className="row">
        <h2 style={{ margin: 0 }}>Audit trail</h2>
        <button onClick={() => act("Verifying hash chain", async () => setChain(await api("/audit/verify")))}>Verify hash chain</button>
        {chain && <span className={`chip ${chain.ok ? "ok" : "bad"}`}>{chain.ok ? `intact, ${chain.checked} events` : `BROKEN at event ${chain.first_bad_id}`}</span>}
      </div>
      <Table
        cols={["Recorded", "Actor", "Action", "Entity", "Detail", "Hash"]}
        rows={events.map((e) => [fmt(e.recorded_at), <code>{e.actor}</code>, e.action, `${e.entity_type} ${e.entity_id.slice(0, 8)}`,
          <span className="small muted">{JSON.stringify(e.detail).slice(0, 160)}</span>, <code>{e.hash.slice(0, 12)}</code>])}
      />
    </div>
  );
}
