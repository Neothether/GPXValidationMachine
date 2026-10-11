"""Drafting agents. Each one calls Gemini for structured output, then writes rows as the vm_agent role.

Agents draft documents. They hold no privilege to write results, evidence or signatures.
"""
from typing import Literal

from pydantic import BaseModel

from . import db, ingest, llm

Level = Literal["low", "medium", "high"]

GROUND_RULES = """You work inside a GxP computer system validation tool (GAMP 5 2nd ed., 21 CFR Part 11,
EU Annex 11, FDA Computer Software Assurance). Rules:
- Draft only from the material you are given. Never invent facts, results, evidence, people or signatures.
- Cite a source for every statement as `path:line` or `path:line-line` from the repository bundle,
  or `intended-use` / `sop` when it comes from those documents.
- If the material does not support something, say so explicitly instead of guessing.
- Write requirements that are specific and testable (one verifiable behaviour each)."""


def _system(conn, system_id) -> dict:
    return conn.execute("select * from systems where id = %s", (system_id,)).fetchone()


def _context(s: dict) -> str:
    return (f"SYSTEM NAME: {s['name']}\n\nINTENDED USE STATEMENT:\n{s['intended_use']}\n\n"
            f"COMPANY CSV SOP:\n{s['sop'] or '(none supplied)'}\n\n"
            f"REPOSITORY BUNDLE (line-numbered):\n{ingest.bundle(s['repo_path'])}")


# --------------------------------------------------------------------------- intake / validation plan
class Profile(BaseModel):
    gxp_impact: Literal["direct", "indirect", "none"]
    gxp_rationale: str
    gamp_category: int
    category_rationale: str
    system_description: str
    scope: list[str]
    out_of_scope: list[str]
    deliverables: list[str]
    roles_and_approvals: list[str]
    acceptance_criteria: list[str]


def run_intake(system_id) -> dict:
    actor = "agent:intake"
    with db.read() as conn:
        s = _system(conn, system_id)
    p, meta = llm.generate(actor, GROUND_RULES, _context(s) + """

TASK: Categorise this system and draft its Validation Plan following the company SOP.
- gxp_impact: does it create, change or store GxP data, given the INTENDED USE (not the code alone)?
- gamp_category: 1 infrastructure, 3 non-configured, 4 configured, 5 custom. Give the rationale.
- deliverables, roles and acceptance criteria must follow the SOP; scale them to the GxP impact.""", Profile)
    content = "\n".join([
        f"# Validation Plan: {s['name']}", "", "## 1. System description", p.system_description, "",
        "## 2. GxP impact", f"**{p.gxp_impact}**. {p.gxp_rationale}", "",
        "## 3. GAMP 5 software category", f"**Category {p.gamp_category}**. {p.category_rationale}", "",
        "## 4. Scope", *[f"- {x}" for x in p.scope], "", "## 5. Out of scope", *[f"- {x}" for x in p.out_of_scope], "",
        "## 6. Deliverables", *[f"- {x}" for x in p.deliverables], "",
        "## 7. Roles and approvals", *[f"- {x}" for x in p.roles_and_approvals], "",
        "## 8. Acceptance criteria", *[f"- {x}" for x in p.acceptance_criteria]])
    with db.tx(actor, db.AGENT) as conn:
        db.audit(conn, "llm_call", "system", system_id, meta)
        conn.execute("update systems set gxp_impact = %s, gamp_category = %s, category_rationale = %s where id = %s",
                     (p.gxp_impact, p.gamp_category, p.category_rationale, system_id))
        return db.create_artifact(conn, system_id, "VP", f"Validation Plan: {s['name']}", content, actor)


# --------------------------------------------------------------------------- URS / FS / DS
class Req(BaseModel):
    req_id: str
    statement: str
    source_ref: str
    parent: str | None = None


class Specs(BaseModel):
    urs: list[Req]
    fs: list[Req]
    ds: list[Req]
    discrepancies: list[str]


def run_specs(system_id) -> list[dict]:
    actor = "agent:spec"
    with db.read() as conn:
        s = _system(conn, system_id)
    specs, meta = llm.generate(actor, GROUND_RULES, _context(s) + """

TASK: Draft the User Requirements (URS), Functional Specification (FS) and Design Specification (DS).
- IDs: URS-001.., FS-001.., DS-001... Every FS has `parent` = one URS id; every DS has `parent` = one FS id.
- URS: what users need, derived from the intended use and user-facing documentation.
- FS: what the system shall do, one testable behaviour each, with exact thresholds, units and boundary
  behaviour as stated in the documentation.
- DS: how it is built or configured (modules, configuration settings and their required values, environment).
- Documentation and intended use define required behaviour. The code is evidence of what was built, not of
  what is required. Where code and documentation disagree, write the requirement from the documentation
  and list the disagreement in `discrepancies` with both citations.
- Include requirements for access control, audit trail and data export where the intended use needs them.
- Aim for 5-8 URS, 10-16 FS, 5-8 DS.""", Specs)

    out = []
    with db.tx(actor, db.AGENT) as conn:
        db.audit(conn, "llm_call", "system", system_id, meta)
        ids: dict[str, str] = {}
        for kind, title, reqs in (("URS", "User Requirements Specification", specs.urs),
                                  ("FS", "Functional Specification", specs.fs),
                                  ("DS", "Design / Configuration Specification", specs.ds)):
            lines = [f"# {title}: {s['name']}", "", "| ID | Requirement | Traces to | Source |", "|---|---|---|---|"]
            lines += [f"| {r.req_id} | {r.statement} | {r.parent or ''} | `{r.source_ref}` |" for r in reqs]
            if kind == "FS" and specs.discrepancies:
                lines += ["", "## Discrepancies between documentation and code",
                          *[f"- {d}" for d in specs.discrepancies]]
            art = db.create_artifact(conn, system_id, kind, f"{title}: {s['name']}", "\n".join(lines), actor)
            for r in reqs:
                row = conn.execute(
                    """insert into requirements (system_id, artifact_id, req_id, level, statement, source_ref, parent_id)
                       values (%s, %s, %s, %s, %s, %s, %s) returning id""",
                    (system_id, art["id"], r.req_id, kind, r.statement, r.source_ref, ids.get(r.parent or ""))).fetchone()
                ids[r.req_id] = row["id"]
            out.append(art)
    return out


# --------------------------------------------------------------------------- risk assessment
class RiskItem(BaseModel):
    req_id: str
    severity: Level
    probability: Level
    detectability: Level
    rationale: str
    citation: str


class RiskAssessment(BaseModel):
    items: list[RiskItem]


_N = {"low": 1, "medium": 2, "high": 3}
TEST_DEPTH = {
    "high": "Rigorous: positive, negative and boundary-value tests; challenge of error handling; independent review of evidence.",
    "medium": "Standard: positive tests plus the main negative case.",
    "low": "Minimal: one positive test confirming the function operates (rely on supplier/unit testing).",
}


def rate(severity: str, probability: str, detectability: str) -> str:
    """GAMP 5 two-step method: risk class from severity x probability, then priority from class x detectability.
    Detectability 'high' means a failure is likely to be noticed, which lowers the priority."""
    klass = _N[severity] * _N[probability]          # 1..9
    klass = 3 if klass >= 6 else 2 if klass >= 3 else 1
    score = klass * (4 - _N[detectability])         # 1..9
    return "high" if score >= 6 else "medium" if score >= 3 else "low"


def run_risk(system_id) -> dict:
    actor = "agent:risk"
    with db.read() as conn:
        s = _system(conn, system_id)
        reqs = conn.execute(
            """select r.* from requirements r join v_current_artifacts a on a.id = r.artifact_id
                where r.system_id = %s and r.level = 'FS' order by r.req_id""", (system_id,)).fetchall()
    listing = "\n".join(f"{r['req_id']}: {r['statement']} [{r['source_ref']}]" for r in reqs)
    ra, meta = llm.generate(actor, GROUND_RULES, f"""SYSTEM: {s['name']}
GxP IMPACT: {s['gxp_impact']}   GAMP CATEGORY: {s['gamp_category']}

INTENDED USE STATEMENT:
{s['intended_use']}

COMPANY CSV SOP (risk scale):
{s['sop'] or '(none supplied)'}

FUNCTIONAL REQUIREMENTS:
{listing}

TASK: Function-level risk assessment, one item per FS requirement above (use the exact req_id).
- severity: impact on patient safety, data integrity or product quality if this function fails,
  GIVEN THE INTENDED USE. The same function is lower severity for an internal convenience tool than for
  one whose output reaches investigators or a regulatory submission.
- probability: likelihood of failure (custom logic, calculations and boundaries are higher than plain display).
- detectability: 'high' if a failure would be obvious to users, 'low' if it would pass silently.
- rationale: 2-3 sentences a QA reviewer can defend. Do NOT default to high: FDA CSA and GAMP 5 expect
  effort scaled to risk, so cosmetic and convenience functions must be rated low.
- citation: the guidance basis, e.g. 'GAMP 5 2nd ed. Appendix M3', 'FDA Computer Software Assurance guidance',
  '21 CFR 11.10(d)', 'EU Annex 11 §12'. Cite only provisions you are sure exist.""", RiskAssessment)

    by_id = {r["req_id"]: r for r in reqs}
    lines = [f"# Risk Assessment: {s['name']}", "",
             "Method: GAMP 5 risk class (severity x probability), then priority (class x detectability). "
             "The rating is computed by the tool from the three assessed factors; the rationale is drafted by the agent.",
             "", "| Function | Sev | Prob | Det | Rating | Test depth | Rationale | Basis |", "|---|---|---|---|---|---|---|---|"]
    with db.tx(actor, db.AGENT) as conn:
        db.audit(conn, "llm_call", "system", system_id, meta)
        rows = []
        for it in ra.items:
            if it.req_id not in by_id:
                continue
            rating = rate(it.severity, it.probability, it.detectability)
            rows.append((it, rating))
            lines.append(f"| {it.req_id} | {it.severity} | {it.probability} | {it.detectability} | **{rating}** | "
                         f"{TEST_DEPTH[rating]} | {it.rationale} | {it.citation} |")
        art = db.create_artifact(conn, system_id, "RA", f"Risk Assessment: {s['name']}", "\n".join(lines), actor)
        for it, rating in rows:
            conn.execute(
                """insert into risks (system_id, artifact_id, requirement_id, severity, probability, detectability,
                                      rating, rationale, test_depth, citation)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (system_id, art["id"], by_id[it.req_id]["id"], it.severity, it.probability, it.detectability,
                 rating, it.rationale, TEST_DEPTH[rating], it.citation))
        return art


# --------------------------------------------------------------------------- IQ / OQ / PQ protocols
class TestCase(BaseModel):
    test_id: str
    protocol: Literal["IQ", "OQ", "PQ"]
    title: str
    requirement_ids: list[str]
    steps: str
    expected: str
    automation: Literal["pytest", "manual"]
    code: str


class Protocols(BaseModel):
    tests: list[TestCase]


def run_protocols(system_id) -> list[dict]:
    actor = "agent:test_author"
    with db.read() as conn:
        s = _system(conn, system_id)
        reqs = conn.execute(
            """select r.*, k.rating, k.test_depth from requirements r
                 join v_current_artifacts a on a.id = r.artifact_id
                 left join risks k on k.requirement_id = r.id
                where r.system_id = %s order by r.level, r.req_id""", (system_id,)).fetchall()
    listing = "\n".join(f"{r['req_id']} [{r['level']}] risk={r['rating'] or 'n/a'}: {r['statement']} [{r['source_ref']}]"
                        for r in reqs)
    protos, meta = llm.generate(actor, GROUND_RULES, _context(s) + f"""

REQUIREMENTS WITH RISK RATINGS:
{listing}

TASK: Write the IQ, OQ and PQ test protocols as automated pytest tests.
- IQ (ids IQ-001..) verifies installation and configuration against DS requirements: runtime version,
  dependencies importable, configuration settings hold their required values.
- OQ (ids OQ-001..) verifies each FS requirement. Depth follows the risk rating:
  high = positive + negative + boundary values (exactly at, just below, just above each threshold);
  medium = positive + main negative; low = one positive test.
- PQ (ids PQ-001..) verifies URS requirements with realistic end-to-end scenarios.
- `requirement_ids`: the exact IDs each test verifies (IQ->DS, OQ->FS, PQ->URS). Every FS and URS needs a test.
- `steps` and `expected`: numbered human-readable procedure and acceptance criteria, as in a paper protocol.
- `code`: a complete, self-contained pytest module (imports included). It runs with the repository root as
  the working directory and on sys.path. One or more `test_` functions; use only the standard library,
  pytest, and the target's own packages.
- Derive expected results from the REQUIREMENT, never from what the code currently does. If the code looks
  wrong against the requirement, still assert the requirement: finding that is the purpose of the test.
- Do not mock the function under test. Do not use skip or xfail.
- `automation`: 'pytest' whenever the behaviour can be checked from Python against this repository
  (calculations, APIs, data and configuration files, environment). Use 'manual' ONLY when it cannot: the
  code is in a language Python cannot import and no data file shows the behaviour, or a person must look at
  a screen. For a manual test leave `code` empty and write `steps` so a tester can follow them exactly,
  saying what to record as evidence (for example a screenshot or an output file).""", Protocols)

    by_id = {r["req_id"]: r["id"] for r in reqs}
    names = {"IQ": "Installation Qualification", "OQ": "Operational Qualification", "PQ": "Performance Qualification"}
    out = []
    with db.tx(actor, db.AGENT) as conn:
        db.audit(conn, "llm_call", "system", system_id, meta)
        for kind in ("IQ", "OQ", "PQ"):
            tests = [t for t in protos.tests if t.protocol == kind]
            if not tests:
                continue
            lines = [f"# {names[kind]} Protocol: {s['name']}", "",
                     "This protocol must be approved before execution. Results are recorded only by the automated runner."]
            for t in tests:
                manual = t.automation == "manual" or not t.code.strip()
                lines += ["", f"## {t.test_id}: {t.title}", f"**Verifies:** {', '.join(t.requirement_ids)}",
                          f"**Execution:** {'manual, by a human tester' if manual else 'automated (pytest)'}", "",
                          "**Procedure**", t.steps, "", "**Acceptance criteria**", t.expected,
                          *([] if manual else ["", "```python", t.code.strip(), "```"])]
            art = db.create_artifact(conn, system_id, kind, f"{names[kind]} Protocol: {s['name']}", "\n".join(lines), actor)
            for t in tests:
                tc = conn.execute(
                    """insert into test_cases (system_id, artifact_id, test_id, title, steps, expected, automation, code)
                       values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
                    (system_id, art["id"], t.test_id, t.title, t.steps, t.expected,
                     "manual" if t.automation == "manual" or not t.code.strip() else "pytest", t.code)).fetchone()
                for rid in t.requirement_ids:
                    if rid in by_id:
                        conn.execute("insert into test_requirements values (%s, %s) on conflict do nothing",
                                     (tc["id"], by_id[rid]))
            out.append(art)
    return out


# --------------------------------------------------------------------------- deviation investigation / fix proposal
class FixProposal(BaseModel):
    investigation: str
    root_cause_location: str
    file: str
    original_snippet: str
    replacement_snippet: str
    is_test_defect: bool


def run_fix_proposal(deviation_id) -> dict:
    """Investigate a failed step and propose a patch. A human decides whether to apply it."""
    actor = "agent:fix"
    with db.read() as conn:
        d = conn.execute(
            """select d.*, tc.test_id, tc.title, tc.code, tc.expected, sr.actual, s.repo_path, s.name
                 from deviations d join test_cases tc on tc.id = d.test_case_id
                 join step_results sr on sr.id = d.step_result_id join systems s on s.id = d.system_id
                where d.id = %s""", (deviation_id,)).fetchone()
    fix, meta = llm.generate(actor, GROUND_RULES, f"""REPOSITORY BUNDLE (line-numbered):
{ingest.bundle(d['repo_path'])}

FAILED TEST {d['test_id']}: {d['title']}
ACCEPTANCE CRITERIA: {d['expected']}

TEST CODE:
{d['code']}

RUNNER OUTPUT:
{d['actual']}

TASK: Investigate this deviation.
- investigation: what failed and why, citing path:line.
- If the application is wrong against its documented requirement, propose the smallest fix:
  `file` (repo-relative path), `original_snippet` (copied EXACTLY from the file, without the line-number
  prefix, enough lines to be unique) and `replacement_snippet`.
- If instead the test is wrong (not the application), set is_test_defect = true, explain, and leave the
  snippets empty. Never propose changing a test or a requirement just to make it pass.""", FixProposal)
    proposal = fix.model_dump_json()
    with db.tx(actor, db.AGENT) as conn:
        db.audit(conn, "llm_call", "deviation", deviation_id, meta)
        return conn.execute("update deviations set investigation = %s, proposed_fix = %s where id = %s returning *",
                            (f"{fix.investigation}\n\nRoot cause: {fix.root_cause_location}", proposal,
                             deviation_id)).fetchone()


# --------------------------------------------------------------------------- supplier assessment
SUPPLIER_QUESTIONS = [
    "Does the supplier operate a documented quality management system, and is it certified or audited?",
    "Is the software developed under a defined development lifecycle with documented requirements and design?",
    "What testing does the supplier perform (unit, integration, system, regression), and is it documented with results?",
    "How are defects tracked, prioritised and resolved, and are known defects published?",
    "How are changes and releases controlled, versioned and communicated to customers (release notes)?",
    "Does the supplier perform code review and maintain source control with traceable history?",
    "How are security vulnerabilities handled, and how quickly are fixes released?",
    "Does the product provide audit trails, access controls and electronic signatures relevant to 21 CFR Part 11 / Annex 11?",
    "What documentation is supplied to the customer (user, administrator, installation, validation support)?",
    "What support, maintenance and end-of-life commitments does the supplier make?",
    "How does the supplier manage its own suppliers, open-source components and their licences?",
    "Does the supplier provide backup, restore, business continuity or data migration support?",
]


class SupplierAnswer(BaseModel):
    question_no: int
    answer: str
    evidence: str
    support: Literal["documented", "partly documented", "not found"]


class SupplierAssessment(BaseModel):
    answers: list[SupplierAnswer]
    reliance: Literal["full", "partial", "none"]
    reliance_rationale: str
    follow_up_questions: list[str]


def run_supplier_assessment(system_id, vendor: str, documents: str = "") -> dict:
    """Draft the supplier questionnaire from the vendor's own documents, with a reliance recommendation.
    Uses pasted vendor documents when given, otherwise the documentation inside the target repository."""
    actor = "agent:supplier"
    with db.read() as conn:
        s = _system(conn, system_id)
    material = (f"VENDOR DOCUMENTS (pasted):\n{documents}" if documents.strip()
                else f"VENDOR MATERIAL (target repository, line-numbered):\n{ingest.bundle(s['repo_path'])}")
    questions = "\n".join(f"{i}. {q}" for i, q in enumerate(SUPPLIER_QUESTIONS, 1))
    sa, meta = llm.generate(actor, GROUND_RULES, f"""SYSTEM: {s['name']}    SUPPLIER: {vendor}
GxP IMPACT: {s['gxp_impact']}   GAMP CATEGORY: {s['gamp_category']}

INTENDED USE:
{s['intended_use']}

{material}

SUPPLIER QUESTIONNAIRE:
{questions}

TASK: Answer every question from the vendor material only (use the question number).
- `evidence`: the path:line citation, or a short exact quote with its document name, that supports the
  answer. If nothing supports it, answer 'Not stated in the supplied material', evidence 'none found',
  support 'not found'. Never assume a practice exists because it is common.
- `reliance`: how far the customer can rely on the supplier's own testing instead of repeating it:
  'full' only if testing, change control and defect handling are all documented with evidence;
  'partial' if some are; 'none' otherwise. Scale to the GxP impact and intended use.
- `follow_up_questions`: what to ask the supplier to close each 'not found' that matters.""", SupplierAssessment)

    by_no = {a.question_no: a for a in sa.answers}
    lines = [f"# Supplier Assessment: {vendor} ({s['name']})", "",
             "Drafted from the supplier's own material. Every answer cites its evidence; unanswered questions are marked.",
             "", "| # | Question | Answer | Evidence | Support |", "|---|---|---|---|---|"]
    for i, q in enumerate(SUPPLIER_QUESTIONS, 1):
        a = by_no.get(i)
        lines.append(f"| {i} | {q} | {a.answer if a else 'Not answered'} | {a.evidence if a else 'none found'} | "
                     f"{a.support if a else 'not found'} |")
    lines += ["", "## Reliance recommendation (draft, for QA decision)", f"**{sa.reliance.capitalize()} reliance.** {sa.reliance_rationale}",
              "", "## Follow-up questions for the supplier", *([f"- {q}" for q in sa.follow_up_questions] or ["- None"])]
    with db.tx(actor, db.AGENT) as conn:
        db.audit(conn, "llm_call", "system", system_id, meta)
        return db.create_artifact(conn, system_id, "SUPPLIER", f"Supplier Assessment: {vendor}", "\n".join(lines), actor)
