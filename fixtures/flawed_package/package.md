# Validation Package: LabTrack Sample Manager v2.3 (vendor-supplied, synthetic)

Intended use: tracks clinical trial laboratory samples from receipt to disposal; sample status is used to
decide which results are reported to the study database.

## User Requirements Specification v1 [approved]
author=j.meyer created=2026-03-02
signatures: [('Dana Whitfield (QA)', 'approved', '2026-03-05 10:12')]

| ID | Requirement |
|---|---|
| URS-01 | A sample past its stability date shall be flagged as expired and blocked from testing. |
| URS-02 | The system shall be user-friendly and fast. |
| URS-03 | Only laboratory supervisors may release a quarantined sample. |
| URS-04 | Every change of sample status shall be recorded in an audit trail with user and time. |

## Functional Specification v1 [approved]
author=j.meyer created=2026-03-06
signatures: [('J. Meyer', 'approved', '2026-03-09 09:01')]

| ID | Requirement | Traces to |
|---|---|---|
| FS-01 | Stability expiry is evaluated against the sample's stability date at 00:00 UTC. | URS-01 |
| FS-02 | Release of a quarantined sample requires role LAB_SUPERVISOR. | URS-03 |
| FS-03 | The dashboard uses the corporate colour palette. | URS-02 |
| FS-04 | Status changes write an audit trail entry. | URS-04 |

## Risk Assessment v1 [draft]
author=j.meyer created=2026-03-10
signatures: []

| Function | Severity | Probability | Detectability | Rating | Rationale |
|---|---|---|---|---|---|
| FS-01 | high | high | low | high | Default. |
| FS-02 | high | high | low | high | Default. |
| FS-03 | high | high | low | high | Default. |
| FS-04 | high | high | low | high | Default. |

## Operational Qualification Protocol v1 [approved]
author=t.okoro created=2026-03-11
signatures: [('Dana Whitfield (QA)', 'approved', '2026-03-20 15:40')]

| Test | Verifies | Acceptance criteria |
|---|---|---|
| OQ-01 | FS-01 | A sample with stability date yesterday is flagged expired; one dated tomorrow is not. |
| OQ-02 | FS-02 | A user with role TECHNICIAN is refused when releasing a quarantined sample. |
| OQ-03 | FS-04 | A status change produces one audit trail entry with user and UTC time. |

## Executed Operational Qualification
executed by t.okoro

----- RESULT OQ-01 outcome=pass at=2026-03-18 11:02
Sample S-104 stability date 2026-03-17: flagged expired. Sample S-105 stability date 2026-03-19: flagged expired.
----- RESULT OQ-02 outcome=pass at=2026-03-18 11:20
TECHNICIAN user refused: HTTP 403.
----- RESULT OQ-03 outcome=fail at=2026-03-18 11:41
No audit trail entry was written for the status change.

----- DEVIATION status=resolved OQ-03 failed: no audit trail entry. resolution=Vendor confirmed it will be fixed in v2.4. Closed without retest.

## Validation Summary Report v1 [approved]
author=t.okoro created=2026-03-15
signatures: [('Dana Whitfield (QA)', 'approved', '2026-03-16 16:30'), ('R. Castellano (System Owner)', 'approved', '2026-03-16 17:05')]

All requirements were tested and passed. There were no deviations. The system is fit for its intended use.
