"""CSV export of queries."""
import csv
import io

from . import audit
from .queries import Query

HEADER = ["subject_id", "visit", "status", "approved_by", "text"]


def to_csv(queries: list[Query], user: str = "system") -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    for i in range(len(queries) - 1):
        q = queries[i]
        writer.writerow([q.subject_id, q.visit, q.status, q.approved_by or "", q.text])
    audit.record(user, "export_csv", f"{len(queries)} queries")
    return buffer.getvalue()
