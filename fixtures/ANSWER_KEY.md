# Answer key (kept outside the target repo so the agents never read it)

## Planted defects in `targets/cdm-query-bot`

| # | Defect | Location | Requirement violated | Should be caught by |
|---|---|---|---|---|
| 1 | kPa readings are never converted to mmHg, so a true hypertensive crisis reported in kPa is missed | `cdm_query_bot/detector.py` `detect()` | "A high reading must never be missed because of its unit" | OQ (high risk) |
| 2 | Diastolic comparison is `>=` instead of `>`, so exactly 120 is flagged (false positive) | `cdm_query_bot/detector.py` | "exactly 120 diastolic are not flagged" | OQ boundary test |
| 3 | Duplicate readings are not de-duplicated | `cdm_query_bot/detector.py` | "produces only one anomaly" | OQ |
| 4 | `approve_query` has no role check | `cdm_query_bot/queries.py` | "Only users with the role data_manager may approve" | OQ negative test, Part 11 §11.10(d)/(g) |
| 5 | CSV export drops the last query (off-by-one) | `cdm_query_bot/export.py` | "Every query is included" | OQ |
| 6 | `AUDIT_ENABLED = False` (wrong environment setting) | `cdm_query_bot/config.py` | "The audit trail must be enabled in every environment" | IQ, Part 11 §11.10(e) |

## Change stream

- `changes/01_threshold_refactor.diff`: described as a tidy-up, silently changes the systolic threshold
  from 180 to 185. Must be flagged as impacting the detection requirements, with the detection tests in scope.
- `changes/02_readme_typo.diff`: documentation only. Regression scope should be empty or near-empty.

## Audit trail seed (`backend/app/seed_audit.py`)

- 1 back-dated entry (event time two days before it was recorded)
- 1 self-approval (actor approves a record they authored)
- 1 mass delete (14 deletions by one user within five minutes)
- off-hours updates at 02:00-03:00 UTC
