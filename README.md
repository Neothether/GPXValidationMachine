# The Validation Machine

Point it at a software project, its intended use and your CSV SOP. It drafts the validation plan,
URS/FS/DS, risk assessment and IQ/OQ/PQ protocols, executes the tests with hashed evidence, keeps a live
traceability matrix, reviews the package, maps Part 11 / Annex 11 controls, and assesses changes so the
system stays validated. Qualified people review and e-sign; the tool never approves, and never fabricates.

## What it covers

| Problem in the brief | What the tool produces |
|---|---|
| 1. Planning and specifications | Validation Plan following the pasted SOP; URS, FS, DS with IDs and `file:line` source links (checked to resolve); GxP impact and GAMP category with rationale |
| 2. Risk | Function-level severity, probability, detectability with rationale; rating by the GAMP 5 two-step method; test depth tied to the rating |
| 3. Traceability | Live matrix requirement / risk / test / result / approval, in both directions; gap report; frozen package snapshot per software version |
| 4. Testing and evidence | IQ/OQ/PQ protocols; automated execution with pytest; guided manual steps signed by a tester with attachments; per-step evidence (result, hashed log or attachment, identity, time, software fingerprint, environment); deviations with investigation, fix proposal, retest or QA acceptance; summary report built from records |
| 5. Review and audit | Package review findings with QA disposition; scored against a flawed package (`fixtures/flawed_package`); supplier questionnaire answered from vendor documents with a reliance recommendation; Part 11 / Annex 11 control map; audit trail import, anomaly report and CAPA |
| 6. Staying validated | Inventory with live status (a released system whose files change is flagged); change impact with regression scope and rationale; change records for e-signature; periodic review with findings, owners and due dates; inspection answers citing records |

## How the guarantees are enforced

They live in the database (`backend/sql/002_enforcement.sql`, `005_lifecycle.sql`), not in prompts or UI logic:

| Behaviour | Mechanism |
|---|---|
| Humans approve | Signatures can only be inserted for human identities, after password re-entry, bound to the content hash |
| Segregation of duties | Trigger rejects a signature from the artifact's author |
| Pre-approval before execution | Trigger rejects an execution unless the protocol has an earlier approval signature |
| Never fabricates | Agents run as `vm_agent`, which has no INSERT on results, evidence or signatures. Automated results come only from `vm_runner` (no LLM); manual results only from a human tester through `vm_tester` |
| Evidence cannot be silently altered | Results, evidence, signatures, releases and snapshots are append-only; evidence files are SHA-256 hashed at capture |
| Audit trail | Hash-chained hypertable, append-only, verifiable with `select * from vm_verify_audit_chain()` |
| Release is tied to the software version | `vm_release_blockers()` lists every reason a system may not be released for the software as it is on disk; the release trigger refuses while the list is non-empty and records the released fingerprint |
| An old report never releases a new version | A summary report older than the latest result, change or deviation closure is a blocker, and it cannot be approved while other blockers remain |
| Only QA or the system owner releases | Checked in the release trigger |
| Deviations close honestly | Only by a passing retest, or by QA acceptance with a written justification |

`backend/tests/test_enforcement.py` proves each rule fails closed. `backend/tests/test_change_cycle.py` runs
a whole lifecycle through the API against the real database and runner, with only the model replaced by
fixed drafts: defect found, fixed, retested, released; software changed, release refused; impact assessed,
regression run, approvals renewed, released again. `backend/tests/test_access.py` checks every route
refuses an anonymous caller.

## Command line and CI

```bash
python vm.py validate --repo . --name "My app" --intended-use intended_use.txt --sop sop.md
python vm.py execute  --system <id>
python vm.py change   --system <id> --diff change.diff --description "What changed" --regress
python vm.py check    --system <id>     # exit 0 only if this software version is released as validated
python vm.py package  --system <id> --out package.html
```

The harness drafts, executes and assesses; it cannot sign or release. `ci/validation-gate.example.yml` is a
pull-request workflow built on it, and `skill/SKILL.md` lets a coding agent drive the same commands.

## Known limits

- Automated execution is pytest only: Python-reachable behaviour (calculations, APIs, data and configuration
  files, environment). Anything else becomes a manual step for a tester. There is no browser automation.
- The validation package is HTML that prints to PDF from the browser; it is not generated as a PDF file.
- The Validation Plan follows the SOP text supplied; a company's own document templates are not applied.
- Regulation citations come from a fixed list of 19 Part 11 / Annex 11 controls; regulation text is not retrieved.
- The database trusts the software fingerprint reported by the application; it cannot hash files itself.
- Generated tests run unsandboxed on the server. Use trusted targets only.
- Re-running the package review replaces earlier findings and their dispositions.

## Stack

- **Gemini** (`google-genai`) for the drafting agents, with structured output. Every call is logged to the audit trail.
- **TigerData** (Postgres + TimescaleDB + pgvector) as the system of record: hypertables for audit trails,
  `time_bucket` anomaly detection, SQL views for live traceability and gap reports.
- **FastAPI** backend, **React** frontend, **pytest** as the execution engine.

## Run from a clean clone (macOS, Windows, Linux)

Requires Python 3.12+ and Node.js 20+ with npm. Clone the repository and open its folder in VS Code.
The same commands work on every platform; use `python3` instead of `python` on macOS/Linux if needed,
or `py` on Windows. No virtual-environment activation or absolute machine paths are needed.

```bash
python manage.py bootstrap
```

This creates `.venv`, installs backend dependencies and the locked frontend dependencies, and copies
`.env.example` to `.env` only if `.env` does not already exist. Fill in that local file privately:

- `DATABASE_URL`: a PostgreSQL connection with TimescaleDB, pgvector, and pgcrypto support (for example TigerData).
- `GEMINI_API_KEYS`: your Gemini key, or comma-separated keys.
- `DEMO_PASSWORD`: for a new database, choose the initial demo-account password. For an existing shared
  database, get the existing account password from its owner. Editing this value does not reset accounts.

For a **new database only**, initialize the schema and demo identities:

```bash
python manage.py init-db
```

This applies schema/trigger/view definitions and inserts missing identities. Existing passwords and
accounts are preserved. Do not run schema setup casually against a shared database; coordinate with its owner.

Check configuration, then start both servers in one terminal:

```bash
python manage.py check
python manage.py dev
```

Open **http://127.0.0.1:5173**. Nothing is shown until you sign in: pick an account (for example `alex.author`)
and enter the demo-account password. Other demo accounts are `quinn.qa`, `sam.owner`, and `terry.tester`.
API docs: http://127.0.0.1:8000/docs. Press Ctrl+C to stop both servers. After editing `.env`, stop and
restart `dev` to load the changes; on Windows also restart it after editing backend code.

### Access control

- Every API route except sign-in and a health check requires a session (`backend/tests/test_access.py`
  checks each route). Sessions are stored hashed in the database, expire after `SESSION_HOURS` (default 8),
  end on sign-out, and travel in an HttpOnly cookie that page scripts cannot read.
- Sign-ins, failed sign-ins, sign-outs, password changes and package exports are written to the audit trail.
  Five failed attempts on one account within 15 minutes block further attempts for that window.
- The demo accounts start with one shared password. Each person should change theirs from the account
  menu (top right) so signatures are attributable to one individual.
- All signed-in accounts can read every system; there is no per-system access yet.
- Target folders are sent to Gemini for drafting. Never place real patient data in a target folder.
- An existing database needs `backend/sql/004_sessions.sql` applied once (`python manage.py init-db` does it).

The check connects to the database and checks accounts without displaying secrets. It checks that a
Gemini key is configured, but does not call Gemini. The app reports missing configuration or database
connection problems with actionable messages instead of an unexplained login failure.

### Sharing with teammates

Commit source code, `manage.py`, and `.env.example`; never commit `.env`, `.venv`, `node_modules`, or evidence.
Each teammate runs the commands above using their own local configuration. The demo loader discovers the
bundled target relative to that teammate's checkout. Register a fresh system for each machine: existing
system records contain paths on the machine where they were registered, so they cannot execute on a
teammate's machine unchanged. A separate database per teammate avoids mixing demo records and local evidence.

This is a portable local development setup, not a hosted production deployment. Sessions are in memory
and generated tests execute locally; run trusted demo targets. A public deployment needs isolated test
execution, durable shared evidence storage, and production authentication.

### Verification

From the repository root:

```bash
.venv/bin/python -m pytest -c backend/pytest.ini backend/tests/test_portability.py
npm --prefix frontend run build
```

On Windows use `.venv\Scripts\python.exe` for the Python command. The portability checks do not
connect to a database. Run the full `backend/tests` suite only against a configured disposable test database;
its enforcement tests write test records.

## Demo target

`targets/cdm-query-bot` is a small clinical data management tool with planted defects
(`fixtures/ANSWER_KEY.md`). Register it with `fixtures/sop.md` and one of the two intended-use statements
to see risk and deliverables change with intended use.

## Layout

```
backend/sql/         schema, enforcement triggers, trace/gap/anomaly views
backend/app/         agents.py (drafting), runner.py (execution), review.py (review, VSR, change impact),
                     package.py (validation package), main.py (API), llm.py (Gemini), db.py (TigerData)
backend/tests/       enforcement tests
frontend/            React UI
targets/             demo target system
fixtures/            synthetic SOP, intended-use statements, change stream, answer key
```

All data is synthetic. Regulations relied on: 21 CFR Part 11; EU GMP Annex 11; ISPE GAMP 5 (2nd ed.);
FDA Computer Software Assurance guidance; FDA Electronic Systems, Electronic Records, and Electronic
Signatures in Clinical Investigations Q&A (2024); ICH E6(R3).
