"""Sign-in and sessions. Every /api route needs a session except those in PUBLIC."""
import os
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from . import db

COOKIE = "vm_session"
PUBLIC = {"/api/login", "/api/health"}
SESSION_HOURS = float(os.environ.get("SESSION_HOURS", "8"))
MAX_FAILURES, WINDOW_MINUTES = 5, 15

router = APIRouter()


def check_password(conn, user_id: str, password: str) -> dict | None:
    return conn.execute(
        "select id, display_name, role from users where id = %s and is_human "
        "and password_hash = crypt(%s, password_hash)", (user_id, password)).fetchone()


def _session_user(token: str) -> dict | None:
    with db.read() as conn:
        return conn.execute(
            """select u.id, u.display_name, u.role from sessions s join users u on u.id = s.user_id
                where s.token_hash = %s and s.revoked_at is null and s.expires_at > clock_timestamp() and u.is_human""",
            (db.sha256(token),)).fetchone()


def require_session(request: Request) -> None:
    """Applied to the whole application: no data is served to a caller who is not signed in."""
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC:
        return
    token = request.cookies.get(COOKIE) or request.headers.get("x-token", "")
    user = _session_user(token) if token else None
    if not user:
        raise HTTPException(401, "Sign in to continue")
    request.state.user, request.state.token = user, token


def current_user(request: Request) -> dict:
    return request.state.user


class Login(BaseModel):
    user_id: str
    password: str


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.post("/api/login")
def login(body: Login, response: Response):
    account = body.user_id.strip()[:100]
    with db.read() as conn:
        failures = conn.execute(
            """select count(*) as n from audit_events where action = 'login_failed' and entity_id = %s
                and recorded_at > clock_timestamp() - make_interval(mins => %s)""", (account, WINDOW_MINUTES)).fetchone()["n"]
        user = check_password(conn, account, body.password) if failures < MAX_FAILURES else None
    if failures >= MAX_FAILURES:
        raise HTTPException(429, f"Too many failed sign-in attempts. Try again in {WINDOW_MINUTES} minutes.")
    if not user:
        with db.tx(account or "unknown", db.APP) as conn:
            db.audit(conn, "login_failed", "user", account or "unknown")
        raise HTTPException(401, "Unknown account or wrong password")     # same answer for both, by design
    token = secrets.token_urlsafe(32)
    with db.tx(user["id"], db.APP) as conn:
        conn.execute("insert into sessions (token_hash, user_id, expires_at) "
                     "values (%s, %s, clock_timestamp() + make_interval(secs => %s))",
                     (db.sha256(token), user["id"], SESSION_HOURS * 3600))
        db.audit(conn, "login", "user", user["id"])
    # HttpOnly: page scripts cannot read the session. Set COOKIE_SECURE=1 when served over HTTPS.
    response.set_cookie(COOKIE, token, max_age=int(SESSION_HOURS * 3600), httponly=True, samesite="strict",
                        secure=os.environ.get("COOKIE_SECURE", "") == "1", path="/")
    return {"user": user}


@router.post("/api/logout")
def logout(request: Request, response: Response):
    user = current_user(request)
    with db.tx(user["id"], db.APP) as conn:
        conn.execute("update sessions set revoked_at = clock_timestamp() where token_hash = %s",
                     (db.sha256(request.state.token),))
        db.audit(conn, "logout", "user", user["id"])
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}


@router.get("/api/me")
def me(request: Request):
    return current_user(request)


class PasswordChange(BaseModel):
    current: str
    new: str


@router.post("/api/password")
def change_password(body: PasswordChange, request: Request):
    user = current_user(request)
    with db.tx(user["id"], db.APP) as conn:
        changed = conn.execute("select vm_change_password(%s, %s, %s, %s) as ok",
                               (user["id"], body.current, body.new, db.sha256(request.state.token))).fetchone()["ok"]
    if not changed:
        raise HTTPException(403, "Current password is incorrect")     # 401 would mean "signed out"
    return {"changed": True}
