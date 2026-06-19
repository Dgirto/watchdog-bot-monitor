"""HMAC heartbeat verification (S-2)."""
import time

import pytest

from adapters.controllers.auth import AuthError, _expected_signature, verify_signature


def _set_mode(monkeypatch, mode, secret=None):
    from infrastructure.config import settings
    import infrastructure.secrets as secrets
    monkeypatch.setattr(settings, "HEARTBEAT_AUTH_MODE", mode)
    if secret is not None:
        monkeypatch.setattr(secrets, "_shared", secret)


def test_off_mode_allows_anything(monkeypatch):
    _set_mode(monkeypatch, "off")
    verify_signature("bot", "prod", None, None)  # no raise


def test_warn_mode_allows_unsigned(monkeypatch):
    _set_mode(monkeypatch, "warn")
    verify_signature("bot", "prod", None, None)  # no raise


def test_enforce_rejects_unsigned(monkeypatch):
    _set_mode(monkeypatch, "enforce")
    with pytest.raises(AuthError):
        verify_signature("bot", "prod", None, None)


def test_enforce_accepts_valid_signature(monkeypatch):
    _set_mode(monkeypatch, "enforce", secret="topsecret")
    ts = str(int(time.time()))
    sig = _expected_signature("topsecret", "bot", "prod", ts)
    verify_signature("bot", "prod", ts, sig)  # no raise


def test_enforce_rejects_bad_signature(monkeypatch):
    _set_mode(monkeypatch, "enforce", secret="topsecret")
    ts = str(int(time.time()))
    with pytest.raises(AuthError):
        verify_signature("bot", "prod", ts, "deadbeef")


def test_enforce_rejects_replay(monkeypatch):
    _set_mode(monkeypatch, "enforce", secret="topsecret")
    old = str(int(time.time()) - 999)
    sig = _expected_signature("topsecret", "bot", "prod", old)
    with pytest.raises(AuthError):
        verify_signature("bot", "prod", old, sig)


def test_enforce_unknown_agent(monkeypatch):
    # enforce mode, no shared secret configured -> unknown agent
    from infrastructure.config import settings
    import infrastructure.secrets as secrets
    monkeypatch.setattr(settings, "HEARTBEAT_AUTH_MODE", "enforce")
    monkeypatch.setattr(secrets, "_shared", None)
    monkeypatch.setattr(secrets, "_per_agent", {})
    ts = str(int(time.time()))
    with pytest.raises(AuthError):
        verify_signature("bot", "prod", ts, "whatever")
