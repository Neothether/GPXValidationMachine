import DOMPurify from "dompurify";
import { marked } from "marked";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError, fmt, setSignedOutHandler } from "./api";

type Act = (label: string, fn: () => Promise<unknown>, done?: string) => Promise<void>;
type Banner = { kind: "ok" | "bad"; title: string; text: string } | null;
type P = { sid: string; rev: number; act: Act };
const Session = createContext({ signedIn: false, busy: "" });
const TABS = ["Pipeline", "Traceability", "Execution", "Deviations", "Review", "Change control", "Release", "Inspection", "Audit trail"] as const;
const TAB_ICONS = ["layers", "link", "play", "alert", "check", "change", "shield", "search", "clock"];
const TAB_DESCRIPTIONS: Record<string, string> = {
  Pipeline: "Move from intended use to a complete validation package.",
  Traceability: "Connect requirements, risks, tests, and supporting evidence.",
  Execution: "Inspect protocol results and verify recorded evidence.",
  Deviations: "Investigate failures, review fixes, and track resolution.",
  Review: "Review your validation package and its supporting controls.",
  "Change control": "Understand change impact and target your regression testing.",
  Release: "See exactly what stands between this software version and release.",
  Inspection: "Answer inspection questions from recorded evidence, and keep the validated state under review.",
  "Audit trail": "Explore recorded activity across the workspace.",
};
function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    layers: <><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></>,
    link: <><path d="m10 14 4-4M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0M16 8l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(0 -1)"/></>,
    play: <path d="m8 4 12 8-12 8V4Z"/>,
    alert: <><path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3v.1"/></>,
    check: <><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></>,
    change: <><path d="M4 7h15l-4-4m5 14H5l4 4M4 7v5m16 5v-5"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>,
    plus: <path d="M12 5v14M5 12h14"/>,
    search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
    doc: <><path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 12h8M8 16h6"/></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/></>,
    close: <path d="m6 6 12 12M18 6 6 18"/>,
    menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] ?? paths.doc}</svg>;
}

type Resource<T> = { data?: T; loading: boolean; error: string; retry: () => void };
function useLoad<T = any>(path: string | null, rev: number): Resource<T> {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string; data?: T; error: string }>({ key: "", error: "" });
  const key = `${path}:${rev}:${attempt}`;
  useEffect(() => {
    let live = true;
    if (path) api<T>(path).then((data) => { if (live) setState({ key, data, error: "" }); })
      .catch((e) => { if (live) setState({ key, error: e.message || "The request failed." }); });
    return () => { live = false; };
  }, [path, key]);
  return { data: state.key === key ? state.data : undefined, loading: !!path && state.key !== key, error: state.key === key ? state.error : "", retry: () => setAttempt((v) => v + 1) };
}
function LoadState({ resources }: { resources: Resource<any>[] }) {
  const failed = resources.filter((r) => r.error);
  return <div className="panel load-state" role={failed.length ? "alert" : "status"}>
    {failed.length ? <><Icon name="alert" size={28}/><h3>We couldn’t load this information</h3><p>{failed.map((r) => r.error).join(" · ")}</p><button onClick={() => failed.forEach((r) => r.retry())}>Try again</button></>
      : <><span className="spinner"/><p>Loading workspace information…</p></>}
  </div>;
}
const pending = (...resources: Resource<any>[]) => resources.some((r) => r.loading || r.error);
const tone = (v?: string | null) =>
  !v ? "" : ["approved", "pass", "validated", "met", "resolved", "low"].includes(v) ? "ok"
    : ["fail", "error", "open", "gap", "critical", "high", "major"].includes(v) ? "bad"
    : ["draft", "partial", "medium", "minor", "change_pending", "in_validation"].includes(v) ? "warn" : "";
// Older evidence logs were captured with terminal colour codes; show them as plain text.
const plain = (log: string) => log.replace(/\x1b\[[0-9;]*m/g, "");
const Chip = ({ v }: { v?: string | null }) => (v ? <span className={`chip ${tone(v)}`}><span className="status-dot"/>{v.replace(/_/g, " ")}</span> : null);
function Table({ cols, rows, empty = "Nothing recorded yet." }: { cols: string[]; rows: ReactNode[][]; empty?: string }) {
  if (!rows.length) return <div className="empty-state"><Icon name="doc" size={28}/><p>{empty}</p></div>;
  return <div className="tablewrap"><table><thead><tr>{cols.map((c, i) => <th key={i} scope="col">{c}</th>)}</tr></thead><tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table></div>;
}

export default function App() {
  const [user, setUser] = useState<any>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  // The open system and tab live in the address bar, so a reload or a shared link returns to the same place.
  const [place] = useState(() => new URLSearchParams(location.hash.slice(1)));
  const [sid, setSid] = useState<string | null>(place.get("system"));
  const [tab, setTab] = useState<(typeof TABS)[number]>(TABS.find((t) => t === place.get("tab")) ?? "Pipeline");
  useEffect(() => { history.replaceState(null, "", sid ? `#system=${sid}&tab=${encodeURIComponent(tab)}` : location.pathname); }, [sid, tab]);
  const [banner, setBanner] = useState<Banner>(null);
  const [busy, setBusy] = useState("");
  const [rev, setRev] = useState(0);
  const [authOpen, setAuthOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const actionRunning = useRef(false);
  const system = useLoad(sid && user ? `/systems/${sid}` : null, rev);
  // Nothing is shown without a session: restore one from the cookie, and leave as soon as the API ends it.
  useEffect(() => {
    setSignedOutHandler(() => { setUser(null); setSid(null); setAuthOpen(false); setBanner(null); });
    api("/me").then(setUser).catch(() => setUser(null)).finally(() => setSessionChecked(true));
  }, []);
  const act: Act = async (label, fn, done) => {
    if (actionRunning.current) return;
    actionRunning.current = true; setBusy(label); setBanner(null);
    try { await fn(); if (done) setBanner({ kind: "ok", title: done, text: "" }); }
    catch (e) { setBanner({ kind: "bad", title: e instanceof ApiError && e.enforced ? "Action needs attention" : "Could not complete this action", text: (e as Error).message }); }
    finally { actionRunning.current = false; setBusy(""); setRev((r) => r + 1); }
  };
  const openSystem = (id: string) => { setSid(id); setTab("Pipeline"); setBanner(null); setMenuOpen(false); };
  const inventory = () => { setSid(null); setBanner(null); setMenuOpen(false); };
  if (!sessionChecked) return <div className="signin-page"><span className="spinner"/></div>;
  if (!user) return <div className="signin-page"><div className="signin-card">
    <div className="signin-brand"><span className="brand-mark"><Icon name="shield" size={25}/></span><div><strong>Validation Machine</strong><span>Sign in to continue</span></div></div>
    <p className="muted">Validation records are available to authorised accounts only.</p>
    <SignInForm onSignedIn={(u) => { setUser(u); setRev((v) => v + 1); }}/>
  </div></div>;
  return <Session.Provider value={{ signedIn: !!user, busy }}>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? "is-open" : ""}`} aria-label="Main navigation">
        <a className="brand" href="#" onClick={(e) => { e.preventDefault(); inventory(); }}><span className="brand-mark"><Icon name="shield" size={25}/></span><span>Validation<span className="brand-sub">MACHINE</span></span></a>
        <div className="nav-caption">WORKSPACE</div>
        <button className={`nav-item ${!sid ? "active" : ""}`} onClick={inventory} aria-current={!sid ? "page" : undefined}><Icon name="grid"/>System inventory</button>
        <div className="nav-caption">VALIDATION WORKSPACE</div>
        <nav>{TABS.map((t, i) => <button key={t} className={`nav-item ${sid && tab === t ? "active" : ""}`} disabled={!sid} aria-current={sid && tab === t ? "page" : undefined} onClick={() => { setTab(t); setMenuOpen(false); }}><Icon name={TAB_ICONS[i]}/>{t}{sid && tab === t && <span className="nav-active-dot"/>}</button>)}</nav>
        {!sid && <p className="nav-hint">Open a system to access its validation workspace.</p>}
        <div className="sidebar-footer"><Icon name="shield"/><div>Evidence at every step<span>Draft · Review · Validate</span></div></div>
      </aside>
      {menuOpen && <button className="sidebar-scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)}/>}
      <div className="workspace">
        <header className="topbar"><button className="icon-button mobile-menu" aria-label="Toggle navigation" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}><Icon name="menu"/></button><div className="breadcrumbs"><button onClick={inventory}>Workspace</button><span>/</span><span>{sid ? system.data?.name ?? "System" : "System inventory"}</span>{sid && <><span>/</span><strong>{tab}</strong></>}</div><button className="profile-button" aria-label={user ? `Account: ${user.display_name}` : "Sign in to your account"} disabled={!!busy} onClick={() => setAuthOpen(true)}><span className="avatar">{user ? user.display_name.slice(0, 2).toUpperCase() : <Icon name="shield" size={17}/>}</span><span>{user?.display_name ?? "Guest access"}<small>{user ? user.role.replace(/_/g, " ") : "Sign in to make changes"}</small></span></button></header>
        <main id="main-content">
          {banner && <div className={`banner ${banner.kind}`} role={banner.kind === "bad" ? "alert" : "status"}><Icon name={banner.kind === "bad" ? "alert" : "check"}/><div><b>{banner.title}</b>{banner.text}</div><button className="icon-button" aria-label="Dismiss notification" onClick={() => setBanner(null)}><Icon name="close" size={16}/></button></div>}
          {busy && <div className="activity-banner" role="status"><span className="spinner"/><div><b>{busy}…</b><span>We’ll refresh the workspace when this finishes.</span></div></div>}
          {!sid ? <Inventory rev={rev} act={act} open={openSystem} signedIn={!!user}/> : <>
            <div className="page-heading"><div><span className="eyebrow">SYSTEM WORKSPACE</span><h1>{tab === "Pipeline" ? "Validation pipeline" : tab}</h1><p>{TAB_DESCRIPTIONS[tab]}</p></div></div>
            <SystemHeader sid={sid} rev={rev} act={act}/>
            <div key={`${sid}:${tab}`}>
              {tab === "Pipeline" && <Pipeline sid={sid} rev={rev} act={act} navigate={setTab}/>}
              {tab === "Traceability" && <Trace sid={sid} rev={rev} act={act}/>}
              {tab === "Execution" && <Executions sid={sid} rev={rev} act={act}/>}
              {tab === "Deviations" && <Deviations sid={sid} rev={rev} act={act}/>}
              {tab === "Review" && <Review sid={sid} rev={rev} act={act}/>}
              {tab === "Change control" && <Changes sid={sid} rev={rev} act={act}/>}
              {tab === "Release" && <ReleasePanel sid={sid} rev={rev} act={act}/>}
              {tab === "Inspection" && <Inspection sid={sid} rev={rev} act={act}/>}
              {tab === "Audit trail" && <Audit rev={rev} act={act}/>}
            </div>
          </>}
          <footer className="workspace-footer">The Validation Machine<span>From requirements to recorded evidence</span></footer>
        </main>
      </div>
    </div>
    {authOpen && <AuthDialog user={user} close={() => setAuthOpen(false)} signIn={(u) => { setUser(u); setAuthOpen(false); setRev((v) => v + 1); }} signOut={async () => { await api("/logout", {}).catch(() => undefined); setUser(null); setSid(null); setAuthOpen(false); }}/>}
  </Session.Provider>;
}

function Modal({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { const previous = document.activeElement as HTMLElement | null; dialog.current?.showModal(); return () => { previous?.focus(); }; }, []);
  return <dialog ref={dialog} className="modal" aria-labelledby="dialog-title" onCancel={(e) => { e.preventDefault(); close(); }}><div className="modal-heading"><h2 id="dialog-title">{title}</h2><button className="icon-button" onClick={close} aria-label="Close dialog"><Icon name="close"/></button></div>{children}</dialog>;
}
// Demo only: the four seeded accounts are offered in a menu. The API still does not list accounts to
// anonymous callers, so replace this with a typed account name outside the demo.
const DEMO_ACCOUNTS = [
  ["alex.author", "Author (CSV specialist)"],
  ["quinn.qa", "Quality Assurance"],
  ["sam.owner", "System Owner"],
  ["terry.tester", "Tester"],
];
function SignInForm({ onSignedIn, label = "Sign in" }: { onSignedIn: (user: any) => void; label?: string }) {
  const [account, setAccount] = useState(DEMO_ACCOUNTS[0][0]);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  // The typed password is cleared before the request, so it never lingers after a failed attempt.
  return <form onSubmit={async (e) => { e.preventDefault(); if (working) return; setWorking(true); setError(""); const typed = password; setPassword(""); try { const r = await api("/login", { user_id: account.trim(), password: typed }); onSignedIn(r.user); } catch (e) { setError((e as Error).message); } finally { setWorking(false); } }}>
    <label htmlFor="account">Sign in as</label><select id="account" value={account} onChange={(e) => setAccount(e.target.value)}>{DEMO_ACCOUNTS.map(([id, role]) => <option key={id} value={id}>{role} · {id}</option>)}</select>
    <label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required/>
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="modal-actions"><button className="primary" disabled={!account.trim() || !password || working}>{working ? "Signing in\u2026" : label}<Icon name="arrow" size={16}/></button></div>
  </form>;
}
function PasswordForm() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [working, setWorking] = useState(false);
  return <form onSubmit={async (e) => { e.preventDefault(); if (working) return; setWorking(true); setNote(null); const body = { current, new: next }; setCurrent(""); setNext(""); try { await api("/password", body); setNote({ ok: true, text: "Password changed. Your other sessions were signed out." }); } catch (e) { setNote({ ok: false, text: (e as Error).message }); } finally { setWorking(false); } }}>
    <label htmlFor="current-password">Current password</label><input id="current-password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required/>
    <label htmlFor="new-password">New password <span className="muted">(at least 10 characters)</span></label><input id="new-password" type="password" autoComplete="new-password" minLength={10} value={next} onChange={(e) => setNext(e.target.value)} required/>
    {note && <p className={note.ok ? "muted" : "form-error"} role={note.ok ? "status" : "alert"}>{note.text}</p>}
    <div className="modal-actions"><button disabled={!current || next.length < 10 || working}>{working ? "Saving\u2026" : "Change password"}</button></div>
  </form>;
}
function AuthDialog({ user, close, signIn, signOut }: { user: any; close: () => void; signIn: (user: any) => void; signOut: () => void }) {
  const [view, setView] = useState<"account" | "switch" | "password">("account");
  return <Modal title="Your account" close={close}>
    <p className="muted">Signed in as {user.display_name} ({user.role.replace(/_/g, " ")}).</p>
    {view === "account" && <div className="modal-actions"><button type="button" onClick={() => setView("password")}>Change password</button><button type="button" onClick={() => setView("switch")}>Switch account</button><button type="button" className="primary" onClick={signOut}>Sign out</button></div>}
    {view === "switch" && <SignInForm onSignedIn={signIn} label="Switch account"/>}
    {view === "password" && <PasswordForm/>}
  </Modal>;
}

function Inventory({ rev, act, open, signedIn }: { rev: number; act: Act; open: (id: string) => void; signedIn: boolean }) {
  const resource = useLoad<any[]>("/systems", rev);
  const systems = resource.data ?? [];
  const [register, setRegister] = useState(false);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const loaded = !pending(resource);
  const visible = systems.filter((s) => `${s.name} ${s.intended_use}`.toLowerCase().includes(search.toLowerCase()) && (status === "all" || s.status === status));
  const stats = [
    { label: "Registered systems", value: systems.length, icon: "grid", note: "Across your workspace", color: "blue" },
    { label: "Validated systems", value: systems.filter((s) => s.status === "validated").length, icon: "check", note: "Released as validated", color: "green" },
    { label: "Open deviations", value: systems.reduce((n, s) => n + Number(s.open_deviations ?? 0), 0), icon: "alert", note: "Investigation or resolution needed", color: "amber" },
    { label: "Traceability gaps", value: systems.reduce((n, s) => n + Number(s.open_gaps ?? 0), 0), icon: "link", note: "Coverage requiring attention", color: "purple" },
  ];
  return <>
    <div className="page-heading"><div><span className="eyebrow">YOUR VALIDATION WORKSPACE</span><h1>System inventory</h1><p>A clear view of your systems, validation progress, and what needs attention.</p></div><button className="primary" onClick={() => setRegister(true)}><Icon name="plus" size={18}/>Register system</button></div>
    <div className="stats-grid">{stats.map((s) => <div className="stat-card" key={s.label}><div className="stat-top"><span>{s.label}</span><span className={`stat-icon ${s.color}`}><Icon name={s.icon}/></span></div><strong>{loaded ? s.value : "—"}</strong><small>{s.note}</small></div>)}</div>
    <section className="panel inventory-panel"><div className="section-heading"><div><h2>All systems <span className="count">{loaded ? systems.length : "—"}</span></h2><p>Open a system to continue its validation journey.</p></div></div>
      <div className="table-toolbar"><div className="search-field"><Icon name="search" size={18}/><input aria-label="Search systems" placeholder="Search systems or intended use…" value={search} onChange={(e) => setSearch(e.target.value)}/></div><select aria-label="Filter systems by status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">All statuses</option>{Array.from(new Set(systems.map((s) => s.status))).map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}</select></div>
      {pending(resource) ? <LoadState resources={[resource]}/> : systems.length === 0 ? <div className="empty-state spacious"><span className="empty-icon"><Icon name="layers" size={30}/></span><h3>Your first validation starts here</h3><p>Register a system and describe its intended use to get started.</p><button className="primary" onClick={() => setRegister(true)}><Icon name="plus" size={16}/>Register your first system</button></div> : <Table cols={["System", "Status", "Classification", "Needs attention", "Last executed", ""]} rows={visible.map((s) => [
        <div className="system-cell"><span className="system-icon"><Icon name="layers"/></span><div><button className="system-name" onClick={() => open(s.id)}>{s.name}</button><span className="system-description">{s.intended_use}</span></div></div>,
        <><Chip v={s.status}/>{s.drift && <span className="chip bad" title="The files have changed since the last release">software changed</span>}</>, <div className="classification">{s.gxp_impact ? `${s.gxp_impact} GxP impact` : "Not assessed"}<small>{s.gamp_category ? `GAMP category ${s.gamp_category}` : "Classification pending"}</small></div>,
        <div className="attention-cell"><span className={s.open_deviations ? "attention" : "muted"}>{s.open_deviations} deviations</span><span className={s.open_gaps ? "attention" : "muted"}>{s.open_gaps} gaps</span></div>,
        <span className="date-cell">{s.last_executed ? fmt(s.last_executed) : "Not executed yet"}</span>,
        <button className="open-button" aria-label={`Open ${s.name}`} onClick={() => open(s.id)}>Open<Icon name="arrow" size={16}/></button>,
      ])} empty="No systems match your search. Try another name or status."/>}
      {loaded && systems.length > 0 && <div className="table-footer">Showing {visible.length} of {systems.length} systems{(search || status !== "all") && <button className="link" onClick={() => { setSearch(""); setStatus("all"); }}>Clear filters</button>}</div>}
    </section>
    <div className="workspace-note"><Icon name="shield"/><p><b>A connected validation record</b>Keep requirements, protocols, evidence, and approvals together throughout the system lifecycle.</p></div>
    {register && <Registration act={act} open={open} signedIn={signedIn} close={() => setRegister(false)}/>}
  </>;
}
function Registration({ act, open, signedIn, close }: { act: Act; open: (id: string) => void; signedIn: boolean; close: () => void }) {
  const { busy } = useContext(Session);
  const [f, setF] = useState({ name: "", repo_path: "", intended_use: "", sop: "" });
  const set = (k: keyof typeof f) => (e: any) => setF({ ...f, [k]: e.target.value });
  const [error, setError] = useState("");
  const demo = (which: string) => act("Loading example", async () => { try { const d = await api("/demo"); setF({ name: `${d.name} (${which})`, repo_path: d.repo_path, intended_use: d.intended_use[which], sop: d.sop }); setError(""); } catch (e) { setError((e as Error).message); throw e; } });
  return <Modal title="Register a system" close={close}><p className="muted">Tell us what the system does and how it will be used. This provides context for its validation plan.</p>
    <div className="demo-options"><span>Try an example</span><button disabled={!!busy} onClick={() => demo("submission")}>FDA submission</button><button disabled={!!busy} onClick={() => demo("internal")}>Internal dashboard</button></div>
    <form onSubmit={(e) => { e.preventDefault(); act("Registering system", async () => { try { const s = await api("/systems", f); close(); open(s.id); } catch (e) { setError((e as Error).message); throw e; } }); }}>
      <label htmlFor="system-name">System name <span>*</span></label><input id="system-name" value={f.name} onChange={set("name")} placeholder="e.g. CDM Query Bot" required/>
      <label htmlFor="repo-path">Repository path <span>*</span></label><input id="repo-path" value={f.repo_path} onChange={set("repo_path")} placeholder="Absolute path to the system repository" required/><small className="field-hint">Use a folder accessible to the backend server.</small>
      <label htmlFor="intended-use">Intended use <span>*</span></label><textarea id="intended-use" value={f.intended_use} onChange={set("intended_use")} placeholder="Who uses this system, for what purpose, and how are its outputs used?" required/>
      <label htmlFor="sop">Company validation SOP <span className="muted">(optional)</span></label><textarea id="sop" value={f.sop} onChange={set("sop")} placeholder="Paste the relevant standard operating procedure…"/>
      {!signedIn && <p className="form-error">Sign in from the workspace header before registering a system.</p>}{error && <p className="form-error" role="alert">{error}</p>}
      <div className="modal-actions"><button type="button" onClick={close}>Cancel</button><button className="primary" disabled={!signedIn || !!busy || !f.name.trim() || !f.repo_path.trim() || !f.intended_use.trim()}>{busy || "Register system"}<Icon name="arrow" size={16}/></button></div>
    </form>
  </Modal>;
}
function SystemHeader({ sid, rev, act }: P) {
  const resource = useLoad(`/systems/${sid}`, rev);
  const { signedIn, busy } = useContext(Session);
  if (pending(resource)) return <LoadState resources={[resource]}/>;
  const s = resource.data;
  if (!s) return null;
  return <section className="panel system-summary"><div className="system-summary-main"><span className="system-icon large"><Icon name="layers" size={25}/></span><div><div className="row"><h2>{s.name}</h2><Chip v={s.status}/></div><p className="muted">{s.intended_use}</p><div className="system-meta"><span>GxP impact <b>{s.gxp_impact ?? "Not assessed"}</b></span><span>GAMP category <b>{s.gamp_category ?? "Pending"}</b></span></div></div></div><div className="system-actions"><a className="button" href={`/api/systems/${sid}/package`} target="_blank" rel="noreferrer"><Icon name="doc" size={16}/>Validation package</a><button disabled={!signedIn || !!busy || s.status === "validated"} onClick={() => act("Updating status", () => api(`/systems/${sid}/release`, {}), "System released as validated")}>{s.status === "validated" ? "Released as validated" : "Release as validated"}</button></div></section>;
}
const STAGES = [
  ["plan", "Plan & classify", "Define intended use, GxP impact, and the validation approach.", ["VP"]],
  ["specs", "Specify", "Connect user needs to functional and design requirements.", ["URS", "FS", "DS"]],
  ["risk", "Assess risk", "Evaluate function-level risks and their rationale.", ["RA"]],
  ["protocols", "Author tests", "Draft installation, operational, and performance protocols.", ["IQ", "OQ", "PQ"]],
  ["vsr", "Summarise", "Bring validation records into a final summary report.", ["VSR"]],
] as const;
function Pipeline({ sid, rev, act, navigate }: P & { navigate: (tab: (typeof TABS)[number]) => void }) {
  const resource = useLoad(`/systems/${sid}`, rev);
  const { signedIn, busy } = useContext(Session);
  const [openId, setOpenId] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  if (pending(resource)) return <LoadState resources={[resource]}/>;
  const arts: any[] = (resource.data?.artifacts ?? []).filter((a: any) => a.current);
  const approved = arts.filter((a) => a.status === "approved").length;
  const stageInfo = STAGES.map(([key, title, description, kinds]) => {
    const docs = arts.filter((a) => (kinds as readonly string[]).includes(a.kind));
    return { key, title, description, kinds, docs, drafted: kinds.every((kind) => docs.some((a) => a.kind === kind)), complete: kinds.every((kind) => docs.some((a) => a.kind === kind && a.status === "approved")) };
  });
  const nextIndex = stageInfo.findIndex((s) => !s.complete);
  const next = stageInfo[nextIndex];
  const reviewDoc = next?.docs.find((a) => a.status !== "approved");
  const run = (stage: typeof stageInfo[number]) => act(`Drafting ${stage.title.toLowerCase()}`, () => api(`/systems/${sid}/run/${stage.key}`, {}), `${stage.title} documents drafted`);
  const visible = arts.filter((a) => filter === "all" || (filter === "pending" ? a.status !== "approved" : a.status === "approved"));
  return <>
    <section className="next-action"><div className="next-action-content"><span className="eyebrow">{next ? "RECOMMENDED NEXT STEP" : "DOCUMENT APPROVALS COMPLETE"}</span><h2>{next ? next.drafted ? `Review ${["the validation plan", "specifications", "the risk assessment", "test protocols", "the summary report"][nextIndex]}` : nextIndex === 0 ? "Start with a validation plan" : `Continue: ${next.title.toLowerCase()}` : "Review the complete validation package"}</h2><p>{next ? next.drafted ? "Your drafts are ready. Review the content and apply the appropriate signatures." : next.description : "Check execution results, deviations, and package findings before release."}</p></div><button className="primary" disabled={!!busy || (!!next && !next.drafted && !signedIn)} onClick={() => next ? next.drafted && reviewDoc ? setOpenId(reviewDoc.id) : run(next) : navigate("Review")}>{next ? next.drafted ? "Review documents" : "Draft with agent" : "Open package review"}<Icon name="arrow" size={17}/></button></section>
    <section className="panel lifecycle-panel"><div className="section-heading"><div><h2>Validation lifecycle</h2><p>A suggested sequence. Review and approve each generated deliverable.</p></div><span className="progress-label">{stageInfo.filter((s) => s.complete).length} of 5 stages approved</span></div><div className="lifecycle-progress" aria-label={`${stageInfo.filter((s) => s.complete).length} of 5 stages approved`}>{stageInfo.map((s, i) => <span key={s.key} className={s.complete ? "complete" : i === nextIndex ? "current" : ""}/>)}</div><div className="stages">{stageInfo.map((stage, i) => <article key={stage.key} className={`stage ${stage.complete ? "complete" : i === nextIndex ? "current" : ""}`}><div className="stage-top"><span className="stage-number">{stage.complete ? <Icon name="check" size={19}/> : `0${i + 1}`}</span><span className="stage-state">{stage.complete ? "Approved" : stage.drafted ? "Needs review" : i === nextIndex ? "Up next" : "Not drafted"}</span></div><h3>{stage.title}</h3><p>{stage.description}</p><div className="stage-docs">{stage.kinds.map((k) => { const a = stage.docs.find((d) => d.kind === k); return <span key={k} title={a ? `${k} v${a.version} · ${a.status}` : `${k} not drafted`} className={`mini-chip ${tone(a?.status)}`}>{k}{a?.status === "approved" && " ✓"}</span>; })}</div>{stage.docs.length > 0 && <button onClick={() => setOpenId((stage.docs.find((a) => a.status !== "approved") ?? stage.docs[0]).id)}>Review deliverables<Icon name="arrow" size={14}/></button>}<button className={stage.docs.length ? "link stage-redraft" : "stage-draft"} disabled={!signedIn || !!busy} onClick={() => run(stage)}>{stage.docs.length ? "Draft new version" : "Draft with agent"}</button></article>)}</div></section>
    <section className="panel deliverables-panel"><div className="section-heading"><div><h2>Deliverables <span className="count">{arts.length}</span></h2><p>{approved} approved · {arts.length - approved} awaiting approval</p></div><select aria-label="Filter deliverables" value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">All deliverables</option><option value="pending">Awaiting approval</option><option value="approved">Approved</option></select></div><Table cols={["Document", "Version", "Status", "Author", "Created", ""]} rows={visible.map((a) => [<div className="document-cell"><span className="document-icon"><Icon name="doc"/></span><div><b>{a.title}</b><small>{a.kind}</small></div></div>, <span className="version">v{a.version}</span>, <Chip v={a.status}/>, <span className="author-label">{a.author_id}</span>, <span className="date-cell">{fmt(a.created_at)}</span>, <button className="open-button" onClick={() => setOpenId(a.id)}>Review<Icon name="arrow" size={15}/></button>])} empty={arts.length ? "No deliverables match this filter." : "Draft your validation plan to create the first deliverable."}/></section>
    {openId && <Modal title="Document review" close={() => setOpenId(null)}><Document aid={openId} rev={rev} act={act}/></Modal>}
  </>;
}

function Document({ aid, rev, act }: { aid: string; rev: number; act: Act }) {
  const resource = useLoad<any>(`/artifacts/${aid}`, rev);
  const a = resource.data;
  const { signedIn, busy } = useContext(Session);
  const [password, setPassword] = useState("");
  const [meaning, setMeaning] = useState("approved");
  // A typed signing password never carries over to another document.
  useEffect(() => setPassword(""), [aid]);
  if (pending(resource)) return <LoadState resources={[resource]}/>;
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
        <select aria-label="Signature meaning" value={meaning} onChange={(e) => setMeaning(e.target.value)}>
          <option value="approved">Approved</option>
          <option value="reviewed">Reviewed</option>
        </select>
        <input aria-label="Password for electronic signature" autoComplete="current-password" type="password" placeholder="Re-enter password to sign" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button className="primary" disabled={!password || !signedIn || !!busy}
                onClick={() => act("Signing", async () => { const typed = password; setPassword(""); await api(`/artifacts/${aid}/sign`, { password: typed, meaning }); }, "Signature applied")}>
          Sign
        </button>
        {executable && (
          <button disabled={!signedIn || !!busy || a.status !== "approved"} onClick={() => act("Executing protocol", () => api(`/artifacts/${aid}/execute`, {}), "Protocol executed; see Execution")}>
            Execute protocol
          </button>
        )}
      </div>
    </div>
  );
}

function Trace({ sid, rev }: P) {
  const trace = useLoad<any[]>(`/systems/${sid}/trace`, rev);
  const gapData = useLoad<any[]>(`/systems/${sid}/gaps`, rev);
  const riskData = useLoad<any[]>(`/systems/${sid}/risks`, rev);
  if (pending(trace, gapData, riskData)) return <LoadState resources={[trace, gapData, riskData]}/>;
  const rows = trace.data ?? [];
  const gaps = gapData.data ?? [];
  const risks = riskData.data ?? [];
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
        <h2>Tests traced back to requirements</h2>
        <Table cols={["Test", "Protocol", "Result", "Verifies"]}
               rows={Array.from(new Set(rows.filter((t) => t.test_id).map((t) => t.test_id))).sort().map((id) => { const hits = rows.filter((t) => t.test_id === id); return [<b>{id}</b>, <>{hits[0].protocol_kind} v{hits[0].protocol_version}</>, <Chip v={hits[0].outcome ?? "not_executed"}/>, hits.map((t) => t.req_id).join(", ")]; })}
               empty="No tests drafted yet." />
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
  const resource = useLoad<any[]>(`/systems/${sid}/executions`, rev);
  const execs = resource.data ?? [];
  const [log, setLog] = useState<string | null>(null);
  const [verified, setVerified] = useState<Record<string, string>>({});
  if (pending(resource)) return <LoadState resources={[resource]}/>;
  return (
    <>
      <ManualTests sid={sid} rev={rev} act={act}/>
      {!execs.length && <div className="panel"><p className="muted">No executions yet. Approve a protocol, then execute it from the Pipeline tab.</p></div>}
      {execs.map((e) => (
        <div className="panel" key={e.id}>
          <div className="row">
            <h2 style={{ margin: 0 }}>{e.kind} v{e.version} {e.retest_of && <span className="chip">retest</span>}</h2>
            <span className="muted small">
              {fmt(e.started_at)} | by <code>{e.runner_id}</code> | software <code>{e.target_fingerprint.slice(0, 16)}</code> | {e.environment.manual ? "manual step" : `Python ${e.environment.python}`} on {e.environment.platform}
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
          {e.results.filter((r: any) => r.id === log).map((r: any) => <pre key={r.id}>{plain(r.actual)}</pre>)}
        </div>
      ))}
    </>
  );
}

function Deviations({ sid, rev, act }: P) {
  const resource = useLoad<any[]>(`/systems/${sid}/deviations`, rev);
  const devs = resource.data ?? [];
  const [retests, setRetests] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  if (pending(resource)) return <LoadState resources={[resource]}/>;
  if (!devs.length) return <div className="panel"><p className="muted">No deviations.</p></div>;
  return (
    <>
      {devs.map((d) => {
        const fix = d.proposed_fix ? JSON.parse(d.proposed_fix) : null;
        return (
          <div className="panel" key={d.id}>
            <div className="row"><h2 style={{ margin: 0 }}>{d.test_id}: {d.title}</h2><Chip v={d.status} /><span className="muted small">opened {fmt(d.opened_at)}</span></div>
            <p>{d.description}</p>
            <pre>{plain(d.actual).slice(-1500)}</pre>
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
            ) : <p><b>{d.status === "accepted" ? "Accepted with justification" : "Resolution"}:</b> {d.resolution} <span className="muted small">by {d.resolved_by}, {fmt(d.resolved_at)}</span></p>}
            {d.status === "open" && <AcceptDeviation id={d.id} act={act}/>}
          </div>
        );
      })}
    </>
  );
}

function Review({ sid, rev, act }: P) {
  const resource = useLoad<any>(`/systems/${sid}/findings`, rev);
  const data = resource.data;
  const [ext, setExt] = useState("");
  const [extFindings, setExtFindings] = useState<any[] | null>(null);
  if (pending(resource)) return <LoadState resources={[resource]}/>;
  return (
    <>
      <div className="panel">
        <div className="row">
          <h2 style={{ margin: 0 }}>Package review</h2>
          <button className="primary" onClick={() => act("Reviewing package", () => api(`/systems/${sid}/run/review`, {}))}>Review this package</button>
        </div>
        <h3>Findings</h3>
        <Table cols={["Severity", "Document", "Location", "Finding", "Recommended action", "Disposition (QA)"]}
               rows={(data?.findings ?? []).map((f: any) => [<Chip v={f.severity} />, f.document, f.location, f.finding, f.recommended_action, <FindingDisposition f={f} act={act}/>])}
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
        <AuditTrailTools sid={sid} events={data?.audit_events ?? 0} act={act}/>
      </div>
      <SupplierPanel sid={sid} act={act}/>
      <div className="panel">
        <h2>Review records</h2>
        <Table cols={["Record", "Version", "Status", "Created", ""]}
               rows={(data?.records ?? []).map((r: any) => [r.title, `v${r.version}`, <Chip v={r.status}/>, fmt(r.created_at), <DocLink aid={r.id} label="Review and sign" rev={rev} act={act}/>])}
               empty="No audit trail review, periodic review or supplier assessment yet." />
      </div>
      <div className="panel">
        <h2>Review an external validation package</h2>
        <textarea placeholder="Paste a vendor or legacy validation package" value={ext} onChange={(e) => setExt(e.target.value)} />
        <p><button disabled={!ext} onClick={() => act("Reviewing external package", async () => setExtFindings((await api(`/systems/${sid}/review-external`, { text: ext })).findings))}>Review pasted package</button></p>
        {extFindings && <Table cols={["Severity", "Document", "Location", "Finding", "Recommended action"]} rows={extFindings.map((f: any) => [<Chip v={f.severity} />, f.document, f.location, f.finding, f.recommended_action])} empty="No problems found in the pasted package." />}
      </div>
    </>
  );
}

function Changes({ sid, rev, act }: P) {
  const resource = useLoad<any[]>(`/systems/${sid}/changes`, rev);
  const changes = resource.data ?? [];
  const [f, setF] = useState({ kind: "code_diff", description: "", diff: "" });
  const demo = () => act("Loading demo change", async () => {
    const d = await api("/demo");
    setF({ kind: "code_diff", description: "Refactor: tidy configuration constants", diff: d.changes["01_threshold_refactor"] });
  });
  if (pending(resource)) return <LoadState resources={[resource]}/>;
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
            {c.withdrawn_at ? <span className="chip">withdrawn</span> : <>
              <span className={`chip ${c.regressed_at ? "ok" : "warn"}`}>{c.regressed_at ? "regressed" : "not regressed"}</span>
              <span className={`chip ${tone(c.record_status)}`}>record {c.record_status ?? "missing"}</span>
              {c.artifact_id && <DocLink aid={c.artifact_id} label="Review and sign record" rev={rev} act={act}/>}
              <button disabled={!!c.regressed_at} onClick={() => act("Running regression", () => api(`/changes/${c.id}/regress`, {}), "Regression recorded; see Execution")}>
                {c.impact?.regression_scope?.length ? "Run regression scope" : "Record: no retest needed"}
              </button>
              <button className="link" disabled={!!c.regressed_at} onClick={() => act("Withdrawing change", () => api(`/changes/${c.id}/withdraw`, {}), "Change withdrawn")}>Withdraw</button>
            </>}
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
  const resource = useLoad<any[]>("/audit?limit=300", rev);
  const events = resource.data ?? [];
  const [chain, setChain] = useState<any>(null);
  if (pending(resource)) return <LoadState resources={[resource]}/>;
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

// ---------------------------------------------------------------------------------------------------
// Lifecycle screens: release decision, manual test steps, review tools, inspection readiness.
// ---------------------------------------------------------------------------------------------------
function DocLink({ aid, label, rev, act }: { aid: string; label: string; rev: number; act: Act }) {
  const [open, setOpen] = useState(false);
  return <><button className="link" onClick={() => setOpen(true)}>{label}</button>{open && <Modal title="Document review" close={() => setOpen(false)}><Document aid={aid} rev={rev} act={act}/></Modal>}</>;
}

function ReleasePanel({ sid, rev, act }: P) {
  const check = useLoad<any>(`/systems/${sid}/release-check`, rev);
  const snaps = useLoad<any[]>(`/systems/${sid}/snapshots`, rev);
  const { busy } = useContext(Session);
  const [label, setLabel] = useState("");
  if (pending(check, snaps)) return <LoadState resources={[check, snaps]}/>;
  const c = check.data;
  const open = c.checklist.filter((r: any) => !r.ok).length;
  return <>
    <section className="panel">
      <div className="section-heading"><div><h2>Release checklist <span className={`chip ${c.ready ? "ok" : "bad"}`}>{c.ready ? "ready to release" : `${open} of ${c.checklist.length} checks open`}</span></h2>
        <p>Judged against the software as it is on disk now. Fingerprint <code>{(c.fingerprint ?? "not recorded").slice(0, 16)}</code>{!c.repo_available && " (the repository is not reachable from this server, so this is the last known version)"}</p></div>
        <button className="primary" disabled={!!busy || !c.ready || c.status === "validated"} onClick={() => act("Releasing", () => api(`/systems/${sid}/release`, {}), "System released as validated")}>{c.status === "validated" ? "Released as validated" : "Release as validated"}</button></div>
      {c.drift && <p className="form-error" role="alert">The software has changed since the last release, so that release no longer covers it. Register the change under Change control.</p>}
      <div className="checklist">{c.checklist.map((row: any) => <div key={row.label} className={`check-row ${row.ok ? "ok" : "bad"}`}>
        <span className="check-mark"><Icon name={row.ok ? "check" : "alert"} size={18}/></span>
        <div><b>{row.label}</b>{row.items.map((b: any, i: number) => <p key={i} className="small"><code>{b.ref}</code> {b.detail}</p>)}</div>
      </div>)}</div>
      <p className="muted small">The database applies the same checks when release is requested, and only Quality Assurance or the System Owner can release.</p>
    </section>
    <section className="panel">
      <h2>Release history</h2>
      <Table cols={["Released", "By", "Software fingerprint", "Summary report"]} rows={c.releases.map((r: any) => [fmt(r.released_at), r.display_name, <code>{r.fingerprint.slice(0, 16)}</code>, `VSR v${r.vsr_version}`])} empty="This system has not been released yet."/>
    </section>
    <section className="panel">
      <div className="section-heading"><div><h2>Package snapshots</h2><p>A frozen copy of the whole validation package for one software version. One is saved automatically at each release.</p></div></div>
      <Table cols={["Saved", "Label", "Software fingerprint", "Package SHA-256", ""]} rows={(snaps.data ?? []).map((s) => [fmt(s.created_at), s.label, <code>{(s.fingerprint ?? "").slice(0, 16)}</code>, <code>{s.sha256.slice(0, 16)}</code>, <a href={`/api/snapshots/${s.id}`} target="_blank" rel="noreferrer">Open</a>])} empty="No snapshot saved yet."/>
      <div className="row" style={{ marginTop: 12 }}><input aria-label="Snapshot label" style={{ flex: 1 }} placeholder="Label, for example: before the Q4 upgrade" value={label} onChange={(e) => setLabel(e.target.value)}/>
        <button disabled={!!busy || !label.trim()} onClick={() => act("Saving snapshot", async () => { await api(`/systems/${sid}/snapshots`, { label }); setLabel(""); }, "Snapshot saved")}>Save snapshot now</button></div>
    </section>
  </>;
}

function ManualTests({ sid, rev, act }: P) {
  const resource = useLoad<any[]>(`/systems/${sid}/manual-tests`, rev);
  const { busy } = useContext(Session);
  const [openId, setOpenId] = useState<string | null>(null);
  const [f, setF] = useState({ outcome: "pass", actual: "", password: "" });
  const [file, setFile] = useState<File | null>(null);
  const tests = resource.data ?? [];
  if (pending(resource) || !tests.length) return null;
  const record = (id: string) => act("Recording manual result", async () => {
    const body: any = { ...f };
    setF({ outcome: "pass", actual: "", password: "" });
    if (file) {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      bytes.forEach((b) => { binary += String.fromCharCode(b); });
      body.attachment_name = file.name;
      body.attachment_base64 = btoa(binary);
    }
    await api(`/test-cases/${id}/manual-result`, body);
    setFile(null); setOpenId(null);
  }, "Manual result recorded and signed");
  return <div className="panel">
    <h2>Manual test steps <span className="count">{tests.length}</span></h2>
    <p className="muted">Steps a person must perform. A tester records what they observed, attaches evidence, and signs by re-entering their password.</p>
    <Table cols={["Test", "Title", "Protocol", "Latest result", ""]} rows={tests.map((t) => [<b>{t.test_id}</b>, t.title, <>{t.kind} v{t.version} <Chip v={t.protocol_status}/></>, t.outcome ? <><Chip v={t.outcome}/> <span className="muted small">{t.recorded_by}, {fmt(t.recorded_at)}</span></> : <Chip v="not_executed"/>, <button onClick={() => setOpenId(openId === t.id ? null : t.id)}>{openId === t.id ? "Close" : "Perform"}</button>])}/>
    {tests.filter((t) => t.id === openId).map((t) => <div key={t.id}>
      <h3>{t.test_id}: procedure</h3><p style={{ whiteSpace: "pre-wrap" }}>{t.steps}</p>
      <h3>Acceptance criteria</h3><p style={{ whiteSpace: "pre-wrap" }}>{t.expected}</p>
      <label htmlFor="manual-actual">Observed result</label><textarea id="manual-actual" value={f.actual} onChange={(e) => setF({ ...f, actual: e.target.value })} placeholder="What you did and what you saw"/>
      <div className="row" style={{ marginTop: 10 }}>
        <select aria-label="Outcome" value={f.outcome} onChange={(e) => setF({ ...f, outcome: e.target.value })}><option value="pass">Pass</option><option value="fail">Fail</option></select>
        <input aria-label="Evidence attachment" type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)}/>
        <input aria-label="Password to sign the result" type="password" autoComplete="current-password" placeholder="Re-enter password to sign" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })}/>
        <button className="primary" disabled={!!busy || f.actual.trim().length < 10 || !f.password} onClick={() => record(t.id)}>Record and sign</button>
      </div>
      <p className="muted small">Only an account with the Tester role can record a manual step, and only for an approved protocol.</p>
    </div>)}
  </div>;
}

function AcceptDeviation({ id, act }: { id: string; act: Act }) {
  const [f, setF] = useState({ justification: "", password: "" });
  return <>
    <label>Accept without a passing retest (Quality Assurance only)</label>
    <div className="row">
      <input aria-label="Justification" style={{ flex: 1 }} placeholder="Written justification, at least 30 characters" value={f.justification} onChange={(e) => setF({ ...f, justification: e.target.value })}/>
      <input aria-label="Password to sign the acceptance" type="password" autoComplete="current-password" placeholder="Re-enter password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })}/>
      <button disabled={f.justification.trim().length < 30 || !f.password} onClick={() => act("Accepting deviation", async () => { const body = f; setF({ justification: "", password: "" }); await api(`/deviations/${id}/accept`, body); }, "Deviation accepted with justification")}>Accept deviation</button>
    </div>
  </>;
}

function FindingDisposition({ f, act }: { f: any; act: Act }) {
  const [note, setNote] = useState("");
  if (f.disposition) return <span><Chip v={f.disposition === "addressed" ? "resolved" : "accepted"}/> <span className="muted small">{f.disposition_note} ({f.disposed_by})</span></span>;
  const send = (disposition: string) => act("Recording disposition", () => api(`/findings/${f.id}/disposition`, { disposition, note }), "Disposition recorded");
  return <div className="row"><input aria-label="Disposition note" placeholder="Note, at least 15 characters" value={note} onChange={(e) => setNote(e.target.value)}/>
    <button disabled={note.trim().length < 15} onClick={() => send("addressed")}>Addressed</button>
    <button disabled={note.trim().length < 15} onClick={() => send("accepted")}>Accept</button></div>;
}

function AuditTrailTools({ sid, events, act }: { sid: string; events: number; act: Act }) {
  const [csv, setCsv] = useState("");
  return <>
    <p className="muted small">{events} audit trail events loaded for this system.</p>
    <label htmlFor="audit-csv">Load an audit trail (CSV with columns recorded_at, event_time, actor, action, record_id, author)</label>
    <textarea id="audit-csv" value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={"recorded_at,event_time,actor,action,record_id,author\n2026-09-30T10:15:00Z,2026-09-28T09:00:00Z,dm.osei,update,reading-2231,"}/>
    <div className="row" style={{ marginTop: 10 }}>
      <button disabled={!csv.trim()} onClick={() => act("Loading audit trail", async () => { await api(`/systems/${sid}/audit-trail`, { csv }); setCsv(""); }, "Audit trail loaded")}>Load CSV</button>
      <button onClick={() => act("Loading synthetic audit trail", () => api(`/systems/${sid}/audit-trail/demo`, {}), "Synthetic audit trail loaded")}>Load synthetic example</button>
      <button className="primary" disabled={!events} onClick={() => act("Drafting audit trail review", () => api(`/systems/${sid}/audit-trail/review`, {}), "Audit trail review with CAPA drafted; see Review records")}>Draft audit trail review with CAPA</button>
    </div>
  </>;
}

function SupplierPanel({ sid, act }: { sid: string; act: Act }) {
  const [f, setF] = useState({ vendor: "", documents: "" });
  return <div className="panel">
    <h2>Supplier assessment</h2>
    <p className="muted">Answers a standard supplier questionnaire from the vendor's own documents, citing evidence, and recommends how far their testing can be relied on. Leave the documents empty to use the documentation inside the target repository.</p>
    <div className="row"><input aria-label="Supplier name" style={{ flex: 1 }} placeholder="Supplier name" value={f.vendor} onChange={(e) => setF({ ...f, vendor: e.target.value })}/></div>
    <label htmlFor="vendor-docs">Vendor documents <span className="muted">(optional)</span></label>
    <textarea id="vendor-docs" value={f.documents} onChange={(e) => setF({ ...f, documents: e.target.value })} placeholder="Paste quality manual excerpts, release notes, test summaries"/>
    <p><button disabled={!f.vendor.trim()} onClick={() => act("Drafting supplier assessment", () => api(`/systems/${sid}/supplier-assessment`, f), "Supplier assessment drafted; see Review records")}>Draft supplier assessment</button></p>
  </div>;
}

function Inspection({ sid, rev, act }: P) {
  const resource = useLoad<any[]>(`/systems/${sid}/inspection`, rev);
  const records = useLoad<any>(`/systems/${sid}/findings`, rev);
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState<any[]>([]);
  if (pending(resource, records)) return <LoadState resources={[resource, records]}/>;
  const reviews = (records.data?.records ?? []).filter((r: any) => r.kind === "PR");
  return <>
    <section className="panel">
      <div className="section-heading"><div><h2>Inspection readiness</h2><p>Each answer is assembled from the records at this moment and names the records it rests on.</p></div></div>
      {(resource.data ?? []).map((a, i) => <div className="qa" key={i}><h3>{a.question}</h3><p>{a.answer}</p><p className="muted small">Records: {a.citations.join("; ")}</p></div>)}
    </section>
    <section className="panel">
      <h2>Ask a question</h2>
      <p className="muted">Answered by an agent from the records only. If the records do not contain the answer, it says so.</p>
      <div className="row"><input aria-label="Inspection question" style={{ flex: 1 }} placeholder="For example: who approved the OQ protocol and was it before execution?" value={question} onChange={(e) => setQuestion(e.target.value)}/>
        <button className="primary" disabled={question.trim().length < 8} onClick={() => act("Answering from records", async () => { const a = await api(`/systems/${sid}/inspection/ask`, { question }); setAsked((list) => [{ question, ...a }, ...list]); setQuestion(""); })}>Ask</button></div>
      {asked.map((a, i) => <div className="qa" key={i}><h3>{a.question}</h3><p>{a.answer}</p><p className="muted small">{a.found_in_records ? `Records: ${a.citations.join("; ")}` : "Not found in the records."}</p></div>)}
    </section>
    <section className="panel">
      <div className="section-heading"><div><h2>Periodic review</h2><p>Confirms the system is still in a validated state: changes, deviations, test status, audit trail and access, with findings, owners and due dates. Signed by Quality Assurance and the System Owner.</p></div>
        <button className="primary" onClick={() => act("Drafting periodic review", () => api(`/systems/${sid}/periodic-review`, {}), "Periodic review drafted")}>Draft periodic review</button></div>
      <Table cols={["Record", "Version", "Status", "Created", ""]} rows={reviews.map((r: any) => [r.title, `v${r.version}`, <Chip v={r.status}/>, fmt(r.created_at), <DocLink aid={r.id} label="Review and sign" rev={rev} act={act}/>])} empty="No periodic review on record."/>
    </section>
  </>;
}
