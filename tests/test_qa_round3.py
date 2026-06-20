"""
QA round 3 — signature binding, per-agent secrets, WS room hygiene, unicode.
AAA pattern.
"""
import time

import pytest

import infrastructure.secrets as secrets
from adapters.controllers.auth import AuthError, _expected_signature, verify_signature
from infrastructure.config import settings


def _enforce(monkeypatch, shared=None, per_agent=None):
    monkeypatch.setattr(settings, "HEARTBEAT_AUTH_MODE", "enforce")
    monkeypatch.setattr(secrets, "_shared", shared)
    monkeypatch.setattr(secrets, "_per_agent", per_agent or {})


# ── Signature must be bound to the bot identity (anti-impersonation) ──
def test_signature_cannot_be_replayed_for_another_bot(monkeypatch):
    # Arrange: shared secret — every agent signs with the same key
    _enforce(monkeypatch, shared="S")
    ts = str(int(time.time()))
    sig_for_a = _expected_signature("S", "bot-a", "prod", ts)
    # Act/Assert: a's signature must NOT authenticate as bot-b
    with pytest.raises(AuthError):
        verify_signature("bot-b", "prod", ts, sig_for_a)
    # ...but it does authenticate bot-a itself
    verify_signature("bot-a", "prod", ts, sig_for_a)


def test_signature_cannot_be_replayed_for_another_environment(monkeypatch):
    _enforce(monkeypatch, shared="S")
    ts = str(int(time.time()))
    sig_prod = _expected_signature("S", "bot-a", "prod", ts)
    with pytest.raises(AuthError):
        verify_signature("bot-a", "staging", ts, sig_prod)  # env is part of the message


# ── Per-agent secrets ─────────────────────────────────────────────
def test_per_agent_secret_isolation(monkeypatch):
    # Arrange: only 'vip' has a secret; no shared fallback
    _enforce(monkeypatch, shared=None, per_agent={"vip": "vip-secret"})
    ts = str(int(time.time()))
    # Correct secret authenticates
    verify_signature("vip", "prod", ts, _expected_signature("vip-secret", "vip", "prod", ts))
    # Wrong secret is rejected
    with pytest.raises(AuthError):
        verify_signature("vip", "prod", ts, _expected_signature("WRONG", "vip", "prod", ts))
    # An agent with no configured secret is unknown
    with pytest.raises(AuthError):
        verify_signature("stranger", "prod", ts, "anything")


# ── WebSocket room hygiene (no connection leak) ───────────────────
async def test_ws_manager_reclaims_empty_rooms():
    from infrastructure.ws_manager import WSManager
    manager = WSManager()
    key, sock = ("b", "prod"), object()
    # Act
    await manager.connect(key, sock)
    assert manager.is_connected(key) is True
    manager.disconnect(key, sock)
    # Assert: the empty room is removed, not left dangling
    assert manager.is_connected(key) is False
    assert key not in manager._rooms


def test_ws_manager_disconnect_unknown_key_is_safe():
    from infrastructure.ws_manager import WSManager
    WSManager().disconnect(("ghost", "prod"), object())  # must not raise


# ── Valid unicode names render (and stay escaped) ─────────────────
def test_unicode_name_accepted_and_rendered(client):
    # Arrange / Act: accented letters are valid (\w with UNICODE)
    resp = client.post("/heartbeat", json={"bot_id": "uni-1", "environment": "prod", "name": "Café-Niño"})
    # Assert
    assert resp.status_code == 200
    assert "Café-Niño" in client.get("/dashboard").text
