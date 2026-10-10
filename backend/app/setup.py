"""Apply the schema and seed identities:  python -m app.setup"""
import os
from pathlib import Path

import psycopg
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

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
    password = os.environ["DEMO_PASSWORD"]
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for path in sorted(SQL_DIR.glob("*.sql")):
            print(f"applying {path.name}")
            conn.execute(path.read_text(encoding="utf-8"))
        for uid, name, role, human in USERS:
            conn.execute(
                """insert into users (id, display_name, role, is_human, password_hash)
                   values (%s, %s, %s, %s, case when %s then crypt(%s, gen_salt('bf')) end)
                   on conflict (id) do update set display_name = excluded.display_name, role = excluded.role,
                       is_human = excluded.is_human, password_hash = excluded.password_hash""",
                (uid, name, role, human, human, password))
        print(f"seeded {len(USERS)} identities")


if __name__ == "__main__":
    main()
