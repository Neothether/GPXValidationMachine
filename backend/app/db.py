"""TigerData access. Every transaction runs as a restricted role with an attributed actor."""
import hashlib
import json
import os
from contextlib import contextmanager

from . import config
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


AGENT, RUNNER, APP = "vm_agent", "vm_runner", "vm_app"
RUNNER_ID = "runner:pytest"

_pool = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(config.required("DATABASE_URL"), min_size=1, max_size=8, timeout=10,
                               kwargs={"row_factory": dict_row, "connect_timeout": 10}, open=True)
    return _pool


@contextmanager
def tx(actor: str, role: str):
    """One transaction as `role`, with `actor` recorded on every audit event it causes."""
    with pool().connection() as conn:
        with conn.transaction():
            conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
            conn.execute("select set_config('vm.actor', %s, true)", (actor,))
            yield conn


@contextmanager
def read():
    with pool().connection() as conn:
        yield conn


def audit(conn, action: str, entity_type: str, entity_id: str, detail: dict | None = None):
    conn.execute("select vm_audit(%s, %s, %s, %s)", (action, entity_type, str(entity_id), Jsonb(detail or {})))


def sha256(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def create_artifact(conn, system_id, kind: str, title: str, content: str, author_id: str) -> dict:
    """Insert the next draft version of a deliverable."""
    version = conn.execute(
        "select coalesce(max(version), 0) + 1 as v from artifacts where system_id = %s and kind = %s",
        (system_id, kind)).fetchone()["v"]
    return conn.execute(
        """insert into artifacts (system_id, kind, version, title, content, content_hash, author_id)
           values (%s, %s, %s, %s, %s, %s, %s) returning *""",
        (system_id, kind, version, title, content, sha256(content), author_id)).fetchone()


def jsonable(rows):
    return json.loads(json.dumps(rows, default=str))
