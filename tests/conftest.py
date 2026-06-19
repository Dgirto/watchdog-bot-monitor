"""
tests/conftest.py — shared fixtures.

Force a sqlite backend with a throwaway DB path *before* the app is imported,
so the module-level container/settings pick it up.
"""
import os
import tempfile

os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "watchdog_test_default.db"))

import pytest
from fastapi.testclient import TestClient


def _fresh_db_path() -> str:
    return tempfile.mktemp(suffix=".db")


@pytest.fixture
def sqlite_repo():
    """Point the sqlite repo module at a fresh DB file. Returns the module."""
    import adapters.repositories.sqlite_repositories as repo
    repo.DB_PATH = _fresh_db_path()
    return repo


@pytest.fixture
def client(sqlite_repo):
    """TestClient with a fresh DB; lifespan runs container.init_db() on it."""
    from main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def enforce_auth(monkeypatch):
    """Switch HMAC verification to enforce mode with a known shared secret."""
    from infrastructure.config import settings
    import infrastructure.secrets as secrets
    monkeypatch.setattr(settings, "HEARTBEAT_AUTH_MODE", "enforce")
    monkeypatch.setattr(secrets, "_shared", "topsecret")
    return "topsecret"
