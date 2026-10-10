"""In-memory audit log."""
from datetime import datetime, timezone

from . import config

_LOG: list[dict] = []


def record(user: str, action: str, detail: str = "") -> None:
    if not config.AUDIT_ENABLED:
        return
    _LOG.append({"user": user, "action": action, "detail": detail,
                 "at": datetime.now(timezone.utc).isoformat()})


def entries() -> list[dict]:
    return list(_LOG)


def clear() -> None:
    _LOG.clear()
