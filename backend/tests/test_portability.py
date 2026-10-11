"""Local-only checks for portable configuration and actionable API failures."""
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
from app import config, db
from app.main import app
from psycopg_pool import PoolTimeout


def test_config_works_from_unrelated_directory(tmp_path):
    backend = str(config.ROOT / "backend")
    code = f"import sys; sys.path.insert(0, {backend!r}); from app.config import ROOT; print(ROOT)"
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert Path(result.stdout.strip()) == config.ROOT


def test_relative_evidence_path_is_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVIDENCE_DIR", "./evidence")
    assert config.evidence_dir() == config.ROOT / "backend/evidence"


def test_missing_database_config_is_actionable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "CHANGE_ME")
    monkeypatch.setattr(db, "_pool", None)
    response = TestClient(app).get("/api/users")
    assert response.status_code == 503
    assert "Set DATABASE_URL" in response.json()["detail"]


def test_database_timeout_does_not_expose_credentials(monkeypatch):
    def unavailable():
        raise PoolTimeout("secret-database-password")
    monkeypatch.setattr(db, "pool", unavailable)
    response = TestClient(app).post("/api/login", json={"user_id": "alex.author", "password": "test"})
    assert response.status_code == 503
    assert "manage.py check" in response.json()["detail"]
    assert "secret-database-password" not in response.text
