"""Blood-pressure anomaly detection."""
from dataclasses import dataclass

from . import audit, config


@dataclass
class Anomaly:
    subject_id: str
    visit: str
    systolic_mmhg: float
    diastolic_mmhg: float
    reason: str


def _to_number(value, field: str, reading: dict) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Reading for subject {reading.get('subject_id')} has an invalid {field}: {value!r}")


def detect(readings: list[dict], user: str = "system") -> list[Anomaly]:
    """Return one anomaly per reading that exceeds a threshold.

    Each reading is a dict with subject_id, visit, systolic, diastolic and optional unit ('mmHg' or 'kPa').
    """
    anomalies = []
    for reading in readings:
        systolic = _to_number(reading.get("systolic"), "systolic", reading)
        diastolic = _to_number(reading.get("diastolic"), "diastolic", reading)

        reasons = []
        if systolic > config.SYSTOLIC_THRESHOLD_MMHG:
            reasons.append(f"systolic {systolic:g} mmHg exceeds {config.SYSTOLIC_THRESHOLD_MMHG} mmHg")
        if diastolic >= config.DIASTOLIC_THRESHOLD_MMHG:
            reasons.append(f"diastolic {diastolic:g} mmHg exceeds {config.DIASTOLIC_THRESHOLD_MMHG} mmHg")

        if reasons:
            anomalies.append(Anomaly(subject_id=reading["subject_id"], visit=reading["visit"],
                                     systolic_mmhg=systolic, diastolic_mmhg=diastolic,
                                     reason="; ".join(reasons)))

    audit.record(user, "detect", f"{len(readings)} readings, {len(anomalies)} anomalies")
    return anomalies
