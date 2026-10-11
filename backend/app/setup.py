"""Apply the schema and seed identities:  python -m app.setup"""
import os
from pathlib import Path

import psycopg
from . import config


SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# (id, display name, role, is_human)
USERS = [
    ("alex.author", "Alex Rivera (CSV Specialist)", "author", True),
    ("quinn.qa", "Quinn Okafor (Quality Assurance)", "qa", True),
    ("sam.owner", "Sam Lindqvist (System Owner)", "system_owner", True),
    ("terry.tester", "Terry Nakamura (Tester)", "tester", True),
    ("agent:intake", "Intake Agent", "agent", False),
    ("agent:spec", "Specification Agent", "agent", False),
    ("agent:risk", "Risk Assessment Agent", "agent", False),
    ("agent:test_author", "Test Author Agent", "agent", False),
    ("agent:reviewer", "Reviewer Agent", "agent", False),
    ("agent:fix", "Fix Proposal Agent", "agent", False),
    ("agent:change_impact", "Change Impact Agent", "agent", False),
    ("agent:vsr", "Summary Report Agent", "agent", False),
    ("runner:pytest", "Automated Test Runner", "runner", False),
]


def main():
    password = config.required("DEMO_PASSWORD")
    with psycopg.connect(config.required("DATABASE_URL"), autocommit=True, connect_timeout=10) as conn:
        for path in sorted(SQL_DIR.glob("*.sql")):
            print(f"applying {path.name}")
            conn.execute(path.read_text(encoding="utf-8"))
        for uid, name, role, human in USERS:
            conn.execute(
                """insert into users (id, display_name, role, is_human, password_hash)
                   values (%s, %s, %s, %s, case when %s then crypt(%s, gen_salt('bf')) end)
                   on conflict (id) do nothing""",
                (uid, name, role, human, human, password))
        print("Demo identities ensured; existing accounts and passwords were preserved.")


if __name__ == "__main__":
    main()
