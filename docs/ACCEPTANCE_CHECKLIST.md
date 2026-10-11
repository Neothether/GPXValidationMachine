# Acceptance checklist

Run once per dataset. Record **pass / fail / n/a** for each item, with a one-line note on any fail.

App: http://127.0.0.1:5173 (start with `python manage.py dev`). Demo signers: `alex.author`, `quinn.qa`,
`sam.owner`, `terry.tester`, all with the shared demo password. Use the user picker in the top bar to switch.

**"Working" means:** all of A, D, E and F1-F3 pass on every dataset; B, C, G and H pass with at most minor
content-quality notes; every fail in F4-F5 is explained. Any failure in E is a blocker.

## A. Start-up
- [ ] A1. UI loads with no error banner; API docs load at http://127.0.0.1:8000/docs.
- [ ] A2. Signing in as `quinn.qa` works; a wrong password is refused.
- [ ] A3. The system inventory lists systems with status, open gaps and open deviations.

## B. Intake and planning (Problem 1)
- [ ] B1. The dataset registers as a system (name, repository path, intended use, SOP) without error.
- [ ] B2. "Plan and categorise" produces a Validation Plan with a GxP impact and a GAMP category, each with a rationale that fits the intended use.
- [ ] B3. "Specify" produces URS, FS and DS; every requirement has a unique ID and a source reference.
- [ ] B4. Spot-check three source references: the cited file and line really say what the requirement says.
- [ ] B5. No requirement is invented: each one can be pointed to in the dataset's material.

## C. Risk (Problem 2)
- [ ] C1. Every FS requirement has severity, probability, detectability, a rating and a written rationale (Traceability tab).
- [ ] C2. Ratings are not all "high"; cosmetic or convenience functions are rated low.
- [ ] C3. The test depth shown for each function matches its rating.

## D. Traceability (Problem 3)
- [ ] D1. The matrix shows requirement, risk, test and result for every requirement.
- [ ] D2. After "Author tests", the gap report shows zero "untested requirement" and zero "orphan test".
- [ ] D3. Before execution the gap report lists tests as "not executed"; after execution that gap clears.

## E. Enforcement (each must be refused with the red "Rejected by the database" banner)
- [ ] E1. Executing a protocol before it is approved.
- [ ] E2. Signing "Approved" as `terry.tester` (a role that cannot approve).
- [ ] E3. The same person signing the same document twice with the same meaning.
- [ ] E4. "Release as validated" before the summary report is approved.
- [ ] E5. Closing a deviation without a passing retest (button stays disabled, or the request is refused).

## F. Testing and evidence (Problem 4)
- [ ] F1. After QA approval, IQ, OQ and PQ each execute and show per-test pass or fail.
- [ ] F2. Each result shows a timestamp, runner identity, software fingerprint, environment and evidence hash; "Show log" displays real pytest output.
- [ ] F3. "Verify evidence hashes" reports intact.
- [ ] F4. Every failed test opened a deviation. Read each log and note whether it is a real defect in the dataset or a mistake in the generated test.
- [ ] F5. Where the dataset has known defects or an answer key: list which were found and which were missed.
- [ ] F6. Deviation flow: investigate, proposed fix shown, apply, retest passes, close with a resolution.

## G. Review and audit (Problem 5)
- [ ] G1. "Review this package" returns findings with severity, document and location; none is obviously false.
- [ ] G2. The Part 11 / Annex 11 map has a status, an evidence citation and a rationale for every rule.
- [ ] G3. The Audit trail tab lists actions with actor and time; "Verify hash chain" reports intact.
- [ ] G4. If the dataset includes an audit trail or a flawed package: the anomalies or findings match what was planted.

## H. Summary and release
- [ ] H1. The summary report drafts, and its counts match the Execution and Deviations tabs.
- [ ] H2. With open gaps or deviations, its conclusion says the records do NOT support release.
- [ ] H3. After QA and system-owner signatures on the summary report, and with no open deviations, "Release as validated" succeeds.
- [ ] H4. The validation package page opens and contains the matrix, evidence, deviations, control map, documents and signatures.

## I. Change control (Problem 6)
- [ ] I1. Submitting a diff returns impacted requirements with reasons, and a regression scope smaller than the full test set.
- [ ] I2. A documentation-only change yields an empty or near-empty scope.
- [ ] I3. "Run regression scope" executes only the scoped tests.

## Notes for testers
- Drafting stages call Gemini and take 15-90 seconds each; wait for the status chip in the top bar to clear.
- VP and VSR need two approvals (QA and system owner). Everything else needs QA only.
- A system registered on one machine stores that machine's repository path, so register your own copy.
- The inventory also lists `enforcement-test` systems created by the automated tests; ignore them.
