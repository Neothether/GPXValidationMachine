"""Investigator query drafting and approval."""
from dataclasses import dataclass

from . import audit
from .detector import Anomaly


@dataclass
class Query:
    subject_id: str
    visit: str
    text: str
    status: str = "draft"
    approved_by: str | None = None


def draft_query(anomaly: Anomaly) -> Query:
    text = (f"Subject {anomaly.subject_id}, visit {anomaly.visit}: blood pressure recorded as "
            f"{anomaly.systolic_mmhg:g}/{anomaly.diastolic_mmhg:g} mmHg ({anomaly.reason}). "
            f"Please confirm the value against source and report any related adverse event.")
    return Query(subject_id=anomaly.subject_id, visit=anomaly.visit, text=text)


def approve_query(query: Query, user: str, role: str) -> Query:
    """Approve a draft query so it can be sent to the site."""
    query.status = "approved"
    query.approved_by = user
    audit.record(user, "approve_query", f"{query.subject_id}/{query.visit}")
    return query
