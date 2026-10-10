# CDM Query Bot

A clinical data management helper. It scans blood-pressure readings captured at study visits, flags
readings that indicate a possible hypertensive crisis, and drafts a data query for the site investigator.

## Required behaviour

### Anomaly detection
- A reading is flagged when systolic pressure is **greater than 180 mmHg** or diastolic pressure is
  **greater than 120 mmHg**. Readings of exactly 180 systolic or exactly 120 diastolic are not flagged.
- Sites may report in mmHg or kPa. Readings in kPa are converted to mmHg (1 kPa = 7.50062 mmHg) before
  the thresholds are applied. A high reading must never be missed because of its unit.
- Readings with a missing or non-numeric value are rejected with an error; they are never silently skipped.
- The same reading submitted more than once (same subject, visit, systolic and diastolic) produces only
  one anomaly, so investigators are not sent duplicate queries.

### Query drafting
- Each anomaly produces one draft query naming the subject, the visit, the measured values in mmHg and
  the threshold that was exceeded.
- Draft queries have status `draft` until approved.

### Access control
- Only users with the role `data_manager` may approve a query. Any other role is refused with a
  `PermissionError` and the query stays in `draft`.

### Export
- Queries can be exported to CSV with a header row and one row per query. Every query is included.

### Audit trail
- Detection runs, query approvals and exports are each written to the audit log with the acting user and
  a UTC timestamp.
- The audit trail must be enabled in every environment (`AUDIT_ENABLED = True`).

## Configuration (`cdm_query_bot/config.py`)

| Setting | Required value |
|---|---|
| `SYSTOLIC_THRESHOLD_MMHG` | 180 |
| `DIASTOLIC_THRESHOLD_MMHG` | 120 |
| `AUDIT_ENABLED` | True |
| `TIMEZONE` | UTC |

## Usage

```python
from cdm_query_bot import detector, queries, export

anomalies = detector.detect(readings, user="dm1")
drafts = [queries.draft_query(a) for a in anomalies]
queries.approve_query(drafts[0], user="dm1", role="data_manager")
print(export.to_csv(drafts, user="dm1"))
```

Requires Python 3.10 or later. No third-party dependencies.
