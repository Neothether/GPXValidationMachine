# Improvement plan (discussion draft, nothing implemented)

Items are added during discussion. No code changes are made until the owner says so.

## Status, 2026-10-11

Built and verified: S1-S7 (access control); R1, R2, R4 (release tied to the software version, visible checklist,
full change cycle as an automated test and once by hand on dataset 01); R3 (source references checked, reviewer
scored against a flawed package: all 10 planted problems raised); R5 (periodic review, supplier assessment,
inspection answers); B3 (deviation acceptance); C1 (summary at the top of the package); C6 partly (prints to PDF);
manual test steps; audit trail import with CAPA; package snapshots; command-line harness, CI example and skill file.

Still open: A2 (regulation text retrieval), A3 (per-document review checklist), B1 (second pass that challenges
generated tests), B2 (coverage from documentation to requirements), C2-C5, C7, C8 (readability), S8 (per-system
access), D2 (non-Python execution), company document templates, browser automation, generated PDF.

## Priority order (agreed in discussion with teammate's review)

0. S1-S4: no data readable without sign-in (owner's request, before anything else).
1. R1 + R2 together: a reliable release decision with a visible checklist.
2. R4: prove one complete change cycle on a real target.
3. R3: verify AI-generated references; measure the reviewer.
4. C1-C3: readable summary, protocols and matrix.
5. R5: periodic review, supplier assessment, inspection view (only if time remains).

## R. Release integrity (from teammate's review)

- R1. Tie release to the exact software version.
  - Current behaviour: every execution records a fingerprint of the target, but the release rule ignores it. The database
    only requires an approved summary report and no open deviations. After a change is registered the system goes to
    `change_pending`, and the old approved report still satisfies the rule, so release can be re-granted without retesting.
  - Also: a change made on disk without being registered is not detected at all.
  - Needed: a release record (system, fingerprint, summary report version, who, when); release refused unless the summary
    report was drafted and approved after the latest change, and the fingerprint at release equals the fingerprint the
    passing results were recorded against.
  - Limit to state openly: the database cannot hash files itself; it trusts the fingerprint the runner reports.
- R2. Missing or outdated evidence blocks release, with the reason shown.
  - Current behaviour: release does not check that the other documents are approved, that tests pass, that gaps are
    closed, or that review findings are addressed. The summary report can be signed even when its own conclusion says
    the records do not support release.
  - Needed: one database function listing release blockers (unapproved required document, requirement with no passing
    result for the current fingerprint, open deviation, unaddressed critical/major finding, change without approved
    change record or without its regression scope executed). The release trigger refuses while the list is non-empty,
    and the UI shows the same list as a checklist.
  - Findings need a disposition (addressed / accepted with justification, by whom) for this to work.
- R3. Verify AI claims.
  - Check every `path:line` source reference resolves to a real file and line range; unresolved ones become review items.
  - Requirement IDs a test cites but that do not exist are dropped silently today; record them as review items instead.
  - Build the flawed validation package with an answer key (planned, never built) and score the reviewer: caught / missed.
- R4. Prove one complete change cycle (acceptance test).
  - Approved package exists -> software changes -> release refused -> impact assessed, regression run, approvals renewed
    -> release succeeds for the new version. Run through the API and database as well as the UI.
  - Not yet exercised live at all: apply fix, retest, close deviation, final release, run regression scope.
- R5. Missing lifecycle features: periodic review (owner, due date, findings, signatures), supplier assessment,
  inspection view answering questions from recorded evidence.

## S. Access control (requested by owner; do before R1/R2)

Status 2026-10-11: S1-S7 built and verified (31 routes refuse anonymous callers; sign-in, reload, restart,
sign-out and package access checked in the browser). Still open: S6 needs each person to change their own
password; S7 does not log individual document reads; S8 and S9 unchanged. Known trade-off: the failed-attempt
lockout lets anyone block an account for 15 minutes by guessing wrongly five times.

- S1. Require sign-in for every read. Today only write actions check the session; every GET endpoint (systems,
  documents, trace, executions and logs, deviations, findings, changes, audit trail, validation package, demo fixtures)
  answers without a token. Apply the session check to the whole API except login and a health check.
- S2. `/api/users` is public because the sign-in picker needs it, so it exposes account names and roles. Replace the
  picker with a typed username, or return nothing until signed in.
- S3. Validation package opens through a plain link, which cannot carry the token. Fetch it with the token and open
  the result, or move sessions to an HttpOnly cookie (which also fixes S5).
- S4. Frontend: remove guest viewing (the redesign shows "Guest access" and the inventory before sign-in); show only
  the sign-in screen until authenticated, and return to it on any 401.
- S5. Sessions: add expiry, server-side sign-out, and survive restarts (currently in memory, never expire).
- S6. One shared demo password for all four accounts defeats attribution. Give each account its own password.
- S7. Audit sign-ins, failed sign-ins and (optionally) reads of documents, as Part 11 11.10(d)/(e) expect.
- S8. Later, if needed: per-system access (who may see which system) and role-based read limits. Not needed for the demo.
- S9. Data boundary to state openly: target contents are sent to Gemini for drafting. Real patient data must never be
  placed in a target folder; the brief requires synthetic or public data only, and a free-tier key gives no
  confidentiality commitment.
- Acceptance test: every API route except login returns 401 without a token (automated test over the route list);
  UI shows nothing but the sign-in screen when signed out.

## A. Make the rules explicit and visible

- A1. Show, per system, exactly which inputs the agents were given: target files read (and files skipped for size or type), intended-use statement, SOP. Today this is invisible to the reviewer.
- A2. Load the actual regulation text (21 CFR Part 11, EU Annex 11, FDA CSA, FDA 2024 Q&A) into the existing `reg_chunks` table and cite passages, instead of relying on the fixed 19-rule list plus the model's memory.
- A3. Add a per-document review checklist the approver must tick before signing (for example for a URS: every requirement testable, every source reference checked). Today the database checks who signs, not whether they reviewed.
- A4. State the approval criteria on each document page: who must sign, what the signature means, what the tool has and has not checked.

## B. Close known quality gaps

- B1. Generated tests can be weaker than their requirement (dataset 01: symptom range of 150 on a 0-100 scale passed OQ-009). Add a second agent pass that challenges each test against its requirement before the protocol goes for approval.
- B2. Requirements coverage: nothing checks that the specifications cover the documentation (dataset 01 had no "transition targets must exist" requirement). Add a coverage pass from source documents to requirements.
- B3. A deviation can only be closed by a passing retest. Add "accepted with justification" (documentation discrepancy, accepted risk) with QA sign-off.
- B4. Reviewer cites the validation tool's own signatures as evidence for target-system controls; prompt tightened, not yet re-verified.

## C. Readability of the output

- C1. One-page executive summary at the top of the validation package: verdict, counts, open items, who signed.
- C2. Plain-language requirement and test titles; move pytest code out of the protocol body into an appendix or collapsible block.
- C3. Traceability matrix: group by user requirement, colour by result, filter by risk and status, collapse passing rows.
- C4. Risk assessment as a heat map (severity x probability) with the rationale on click.
- C5. Deviations written as a short narrative: what was expected, what happened, why, what was done.
- C6. Real PDF export with cover page, table of contents, page numbers, and signature blocks.
- C7. Audit trail in sentences ("Quinn Okafor approved OQ v1 at 03:18 UTC") instead of raw JSON.
- C8. Glossary tooltips for CSV terms (URS, OQ, GAMP category) for non-specialist readers and judges.

## D. Operations

- D1. Gemini quota: paid key or one key per teammate; the free tier (20 requests/day per model) cannot support a demo.
- D2. Non-Python targets cannot be executed (pytest only). Decide whether to support another runner or state the limit.
