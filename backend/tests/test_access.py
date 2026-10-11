"""No data without sign-in: every API route except the public ones must refuse an anonymous caller."""
import os
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth
from app.main import app

client = TestClient(app)
DUMMY = "00000000-0000-0000-0000-000000000000"
ROUTES = [(method, re.sub(r"\{[^}]+\}", DUMMY, route.path))
          for route in app.routes if isinstance(route, APIRoute) and route.path.startswith("/api/")
          for method in sorted(route.methods - {"HEAD", "OPTIONS"})]


def test_route_list_is_not_empty():
    assert len(ROUTES) > 25


@pytest.mark.parametrize("method,path", [r for r in ROUTES if r[1] not in auth.PUBLIC])
def test_anonymous_caller_is_refused(method, path):
    response = client.request(method, path, json={})
    assert response.status_code == 401, f"{method} {path} answered {response.status_code} without a session"


def test_only_login_and_health_are_public():
    assert auth.PUBLIC == {"/api/login", "/api/health"}
    assert client.get("/api/health").status_code == 200


needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


@needs_db
def test_forged_and_revoked_sessions_are_refused():
    assert client.get("/api/systems", headers={"X-Token": "not-a-real-session"}).status_code == 401
    assert client.get("/api/systems", cookies={auth.COOKIE: "not-a-real-session"}).status_code == 401


@needs_db
def test_sign_in_sign_out_cycle():
    password = os.environ["DEMO_PASSWORD"]
    with TestClient(app) as c:
        # a made-up account, so repeated test runs never lock a real one out
        assert c.post("/api/login", json={"user_id": "test.no-such-account", "password": "definitely-wrong"}).status_code in (401, 429)
        r = c.post("/api/login", json={"user_id": "quinn.qa", "password": password})
        assert r.status_code == 200 and "token" not in r.json()
        assert "httponly" in r.headers["set-cookie"].lower()
        assert c.get("/api/me").json()["id"] == "quinn.qa"
        assert c.get("/api/systems").status_code == 200
        assert c.post("/api/logout").status_code == 200
        assert c.get("/api/systems").status_code == 401
