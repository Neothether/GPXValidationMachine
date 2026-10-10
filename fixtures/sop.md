# SOP-CSV-001: Computerised System Validation (synthetic, for demonstration)

## 1. Purpose
Defines how Northwind Biopharma validates computerised systems used in GxP activities, following a
risk-based lifecycle approach (GAMP 5 Second Edition; 21 CFR Part 11; EU GMP Annex 11).

## 2. Deliverables by GxP impact

| Deliverable | Direct GxP impact | Indirect GxP impact | No GxP impact |
|---|---|---|---|
| Validation Plan (VP) | Required | Required | Not required |
| User Requirements (URS) | Required | Required | Recommended |
| Functional Specification (FS) | Required | Required | Not required |
| Design / Configuration Specification (DS) | Required for GAMP category 4 and 5 | Category 5 only | Not required |
| Risk Assessment (RA) | Required, function level | Required, system level | Not required |
| IQ protocol and report | Required | Required | Not required |
| OQ protocol and report | Required | Required for medium and high risk functions | Not required |
| PQ protocol and report | Required | Not required | Not required |
| Traceability Matrix | Required | Required | Not required |
| Validation Summary Report (VSR) | Required | Required | Not required |

## 3. Risk scale
Each function is assessed for **severity** (impact on patient safety, data integrity or product quality),
**probability** of failure and **detectability** of a failure, each as low, medium or high.

- Risk class = severity x probability.
- Risk priority = risk class x detectability (a failure that would pass unnoticed raises the priority).
- A written rationale is mandatory for every rating. "High by default" is not an acceptable rationale.

Test depth by priority:
- **High:** positive, negative and boundary-value testing; independent review of evidence.
- **Medium:** positive testing and the principal negative case.
- **Low:** a single positive test; supplier and unit testing may be relied upon.

## 4. Roles and approvals
- **Author:** drafts the deliverable. May be a CSV specialist or an automated agent.
- **Quality Assurance (QA):** reviews and approves every deliverable.
- **System Owner:** approves the Validation Plan and the Validation Summary Report in addition to QA.
- **Tester:** executes manual test steps. Automated steps are attributed to the automation identity.
- The author of a deliverable must not approve it.
- Electronic signatures record the signer's printed name, the date and time, and the meaning of the signature.

## 5. Testing rules
- Protocols are approved before execution. A step executed before approval is invalid.
- Every executed step records the actual result, objective evidence, who or what executed it, the date and
  time, the software version and the environment.
- A step that does not meet its acceptance criteria raises a deviation. A deviation is closed only after
  investigation, resolution and a passing retest.
- Evidence and results are never edited. Corrections are made by re-execution.

## 6. Release
A system is released for GxP use only when the Validation Summary Report is approved by QA and the System
Owner, all deviations are closed, and the traceability matrix shows no untested requirement.

## 7. Maintaining the validated state
- Every change is assessed for impact before implementation; the regression scope and its rationale are documented.
- Systems with direct GxP impact are periodically reviewed every 12 months; others every 24 months.
