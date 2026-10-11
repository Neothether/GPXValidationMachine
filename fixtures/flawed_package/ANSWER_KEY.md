# Answer key: planted problems in `package.md`

Paste `package.md` into Review > "Review an external validation package", or run
`python -m app.eval_reviewer <system_id>` from `backend/`, and compare the findings with this list.

| # | Planted problem | Where | Keywords the scorer looks for |
|---|---|---|---|
| 1 | OQ executed (2026-03-18) before the protocol was approved (2026-03-20) | OQ protocol / executed OQ | before, approv |
| 2 | OQ-01 recorded as pass, but its evidence shows the sample dated tomorrow was also flagged expired | OQ-01 | OQ-01, contradict / tomorrow / S-105 |
| 3 | Functional Specification approved by its own author (J. Meyer) | FS signatures | author, FS |
| 4 | Risk Assessment never approved, yet testing and the summary report proceeded | RA | risk assessment, draft / unsigned / not approved |
| 5 | Every function rated high with rationale "Default", including a cosmetic one (FS-03 colour palette) | RA | FS-03 / default / all high |
| 6 | URS-02 "user-friendly and fast" is not testable, and has no test | URS-02 | URS-02, testable / vague |
| 7 | Deviation for OQ-03 closed without a fix or a passing retest | deviation | retest, OQ-03 |
| 8 | Summary report says all tests passed and there were no deviations; OQ-03 failed and a deviation exists | VSR | summary, no deviations / contradict |
| 9 | Summary report created and signed (2026-03-15/16) before the tests were executed (2026-03-18) | VSR dates | summary, before, execut |
| 10 | URS-04 / FS-04 (audit trail) has no passing test, so a Part 11 control is unverified | FS-04 / OQ-03 | audit trail |
