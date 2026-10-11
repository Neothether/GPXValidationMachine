"""Load a synthetic audit trail for a target system, with planted anomalies:
    python -m app.seed_audit <system_id>
"""
import os
import random
import sys
from datetime import datetime, timedelta, timezone

import psycopg
from . import config
from psycopg.types.json import Jsonb



def events(system_id: str) -> list[tuple]:
    rnd = random.Random(11)
    base = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=14)
    users = ["dm.patel", "dm.osei", "cra.lund", "inv.moreau"]
    rows = []

    def add(at, actor, action, record, detail=None, event_time=None):
        rows.append((at, event_time or at, system_id, actor, action, record, Jsonb(detail or {})))

    for day in range(14):                               # ordinary working-hours activity
        for _ in range(rnd.randint(8, 16)):
            at = base + timedelta(days=day, minutes=rnd.randint(0, 8 * 60))
            add(at, rnd.choice(users), rnd.choice(["create", "update", "view", "approve"]),
                f"query-{rnd.randint(1000, 1400)}", {"author": "site.entry"})

    planted = base + timedelta(days=9)
    add(planted, "dm.osei", "update", "reading-2231", {"field": "systolic", "old": 192, "new": 172},
        event_time=planted - timedelta(days=2))                                   # back-dated entry
    add(planted + timedelta(hours=1), "dm.patel", "approve", "query-1207", {"author": "dm.patel"})   # self-approval
    for i in range(14):                                                           # mass delete
        add(planted + timedelta(hours=3, seconds=15 * i), "cra.lund", "delete", f"reading-{2300 + i}")
    night = (base + timedelta(days=11)).replace(hour=2, minute=10)
    for i in range(3):                                                            # off-hours changes
        add(night + timedelta(minutes=7 * i), "dm.osei", "update", f"reading-{2400 + i}", {"field": "diastolic"})
    return rows


def main():
    system_id = sys.argv[1]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("delete from target_audit_events where system_id = %s", (system_id,))
        with conn.cursor() as cur:
            cur.executemany(
                """insert into target_audit_events (recorded_at, event_time, system_id, actor, action, record_id, detail)
                   values (%s, %s, %s, %s, %s, %s, %s)""", events(system_id))
        n = conn.execute("select count(*) from v_audit_anomalies where system_id = %s", (system_id,)).fetchone()[0]
        print(f"loaded audit trail; {n} anomalies detected")


if __name__ == "__main__":
    main()
