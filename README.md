# The Validation Machine

Point it at a software project, its intended use and your CSV SOP. It drafts the validation plan,
URS/FS/DS, risk assessment and IQ/OQ/PQ protocols, executes the tests with hashed evidence, keeps a live
traceability matrix, reviews the package, maps Part 11 / Annex 11 controls, and assesses changes so the
system stays validated. Qualified people review and e-sign; the tool never approves, and never fabricates.

## How the guarantees are enforced

They live in the database (`backend/sql/002_enforcement.sql`), not in prompts or UI logic:

| Behaviour | Mechanism |
|---|---|
| Humans approve | Signatures can only be inserted for human identities, after password re-entry, bound to the content hash |
| Segregation of duties | Trigger rejects a signature from the artifact's author |
| Pre-approval before execution | Trigger rejects an execution unless the protocol has an earlier approval signature |
| Never fabricates | Agents run as `vm_agent`, which has no INSERT on results, evidence or signatures. Only `vm_runner` (no LLM) does |
| Evidence cannot be silently altered | Results, evidence and signatures are append-only; evidence files are SHA-256 hashed at capture |
| Audit trail | Hash-chained hypertable, append-only, verifiable with `select * from vm_verify_audit_chain()` |
| Never "validated" without QA | Trigger requires an approved Validation Summary Report and no open deviations |

`backend/tests/test_enforcement.py` proves each one fails closed.

## Stack

- **Gemini** (`google-genai`) for the drafting agents, with structured output. Every call is logged to the audit trail.
- **TigerData** (Postgres + TimescaleDB + pgvector) as the system of record: hypertables for audit trails,
  `time_bucket` anomaly detection, SQL views for live traceability and gap reports.
- **FastAPI** backend, **React** frontend, **pytest** as the execution engine.

## Run from a clean clone

Requires Python 3.12+ and Node 20+.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements.txt
copy .env.example .env            # then fill in DATABASE_URL, GEMINI_API_KEYS, DEMO_PASSWORD
cd backend
python -m app.setup               # schema, enforcement, views, demo identities
pytest                            # enforcement tests
uvicorn app.main:app --reload     # API on http://localhost:8000  (docs at /docs)
```

```bash
cd frontend
npm install
npm run dev                       # UI on http://localhost:5173
```

Demo signers (`alex.author`, `quinn.qa`, `sam.owner`, `terry.tester`) all use `DEMO_PASSWORD` from `.env`.

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
