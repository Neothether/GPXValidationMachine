---
name: gxp-validation
description: Validate a software repository for GxP use with The Validation Machine, and keep it validated after changes. Use when asked to validate a system, produce a validation package (URS, FS, risk assessment, IQ/OQ/PQ, traceability, summary report), assess the validation impact of a change, or check whether a software version is released as validated.
---

# GxP validation with The Validation Machine

You drive the command-line harness (`python vm.py`, in the Validation Machine repository). It drafts
documents, runs tests and records evidence in a database that enforces the compliance rules.

## Rules you must not work around

- You never approve, sign or release. Those are done by a person in the web app. The database refuses
  them from the pipeline identity; do not look for another way.
- You never write or edit a test result, an evidence file or a signature. Results come only from the runner.
- If a test fails, report it. Do not change a test or a requirement to make it pass.
- Use synthetic or public data only. Never place real patient data in a repository being validated:
  its contents are sent to a model for drafting.

## Validate a repository for the first time

1. Ask the user for the intended-use statement (who uses the system, for what, with what data, and whether
   its output reaches patients, investigators or a regulatory submission) and the company CSV SOP, if you
   were not given them. Save each to a text file. Do not write the intended use yourself.
2. Draft the deliverables:
   `python vm.py validate --repo <path> --name "<system name>" --intended-use intended_use.txt --sop sop.md`
   Note the system id it prints.
3. Stop and tell the user which documents need review and e-signature in the web app (plan, URS, FS, DS,
   risk assessment, IQ, OQ, PQ). Tests cannot run before the protocols are approved.
4. When they confirm approval: `python vm.py execute --system <id>`. Report passes, failures and the
   deviations opened. Each deviation is investigated and closed in the web app.
5. `python vm.py check --system <id>` prints the release checklist with every open blocker. Relay it.
   Release itself is done by Quality Assurance or the System Owner.

## After a code or configuration change

1. Produce the diff (for example `git diff main...HEAD > change.diff`).
2. `python vm.py change --system <id> --diff change.diff --description "<what changed>" --regress`
   This lists the impacted requirements with reasons, the regression scope, and runs exactly that scope.
3. Report the result and what remains for people: approve the change record, draft and sign a new summary
   report, release. An earlier approved report never releases a new software version.

## Check a version

`python vm.py check --system <id>` exits 0 only if the software on disk is the version released as
validated. Use it as a gate before deployment.

## Export

`python vm.py package --system <id> --out validation-package.html` writes the full package: summary,
traceability matrix, executed tests with evidence hashes, deviations, control map, every document and
signature. It prints to PDF from a browser.
