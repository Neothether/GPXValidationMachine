"""Measure the reviewer agent against a deliberately flawed package with a known answer key:

    python -m app.eval_reviewer <system_id>

Uses one model call. The system id only attributes the call in the audit trail; nothing of the system changes.
The score is a keyword match against fixtures/flawed_package/ANSWER_KEY.md, so read the findings as well.
"""
import sys

from . import config, review

PLANTED = [
    ("Executed before the protocol was approved", [["before", "prior to"], ["approv"], ["execut", "oq"]]),
    ("OQ-01 pass contradicts its evidence", [["oq-01"], ["contradict", "tomorrow", "s-105", "inconsisten", "should not"]]),
    ("FS approved by its own author", [["author", "self-approv", "meyer"], ["fs", "functional"]]),
    ("Risk assessment never approved", [["risk assessment", "ra "], ["draft", "unsigned", "not approved", "no signature", "unapproved", "missing"]]),
    ("Everything rated high by default", [["default", "all high", "fs-03", "every function"], ["high", "risk"]]),
    ("URS-02 is not testable", [["urs-02"], ["testable", "vague", "measur", "subjective"]]),
    ("Deviation closed without retest", [["retest", "without a fix", "closed without"], ["oq-03", "deviation"]]),
    ("Summary contradicts the results", [["summary", "vsr"], ["no deviation", "contradict", "all requirements", "passed", "inaccurate", "false"]]),
    ("Summary signed before execution", [["summary", "vsr"], ["before", "prior to"], ["execut", "test"]]),
    ("Audit trail requirement unverified", [["audit trail"], ["fail", "unverified", "no passing", "not verified", "fs-04", "urs-04"]]),
]


def main():
    system_id = sys.argv[1]
    text = (config.ROOT / "fixtures" / "flawed_package" / "package.md").read_text(encoding="utf-8")
    findings = review.review_external(system_id, text)["findings"]
    blob = [f"{f['document']} {f['location']} {f['finding']}".lower() for f in findings]
    caught = 0
    for name, groups in PLANTED:
        hit = any(all(any(word in line for word in group) for group in groups) for line in blob)
        caught += hit
        print(f"  [{'caught' if hit else 'MISSED'}] {name}")
    print(f"\n{caught} of {len(PLANTED)} planted problems caught; {len(findings)} findings raised in total:")
    for f in findings:
        print(f"  ({f['severity']}) {f['document']} / {f['location']}: {f['finding'][:160]}")


if __name__ == "__main__":
    main()
