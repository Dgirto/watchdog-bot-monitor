"""
Adversarial QA suite — boundary analysis, negative paths, hostile inputs.

Organised as: POSITIVE, BOUNDARY, NEGATIVE/MALICIOUS, ROBUSTNESS.
All tests follow Arrange–Act–Assert.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest

from adapters.controllers.auth import AuthError, _expected_signature, verify_signature
from adapters.controllers.dashboard import _gauge
from domain.entities.bot import Bot, BotEnvironment, BotStatus, Incident
from use_cases.health import RecordHealthUseCase


# ════════════════════════════ POSITIVE ════════════════════════════

def test_heartbeat_happy_path_registers_and_marks_online(client):
    # Arrange / Act
    resp = client.post("/heartbeat", json={"bot_id": "ok-bot", "environment": "prod", "name": "OK"})
    # Assert
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "online" and body["registered"] is True


def test_health_metrics_sanitized_and_stored(client):
    # Arrange / Act
    with client.websocket_connect("/ws/agent?bot_id=ai-ok&environment=prod") as ws:
        ws.send_json({"type": "health", "seq": 1, "metrics": {
            "inference_latency_p95_ms": 500, "llm_error_rate": 0.02, "queue_depth": 7}})
        assert ws.receive_json()["type"] == "ack"
    # Assert
    row = client.get("/agents/ai-ok/health?environment=prod").json()[0]
    assert row["queue_depth"] == 7 and row["inference_latency_p95_ms"] == 500


# ════════════════════════════ BOUNDARY ════════════════════════════

def test_bot_id_max_length_64_accepted(client):
    resp = client.post("/heartbeat", json={"bot_id": "a" * 64, "environment": "prod"})
    assert resp.status_code == 200


def test_bot_id_length_65_rejected(client):
    resp = client.post("/heartbeat", json={"bot_id": "a" * 65, "environment": "prod"})
    assert resp.status_code == 422


def test_name_max_length_80_accepted(client):
    resp = client.post("/heartbeat", json={"bot_id": "b1", "environment": "prod", "name": "n" * 80})
    assert resp.status_code == 200


def test_name_length_81_rejected(client):
    resp = client.post("/heartbeat", json={"bot_id": "b1", "environment": "prod", "name": "n" * 81})
    assert resp.status_code == 422


def test_is_stale_exactly_at_threshold_is_not_stale():
    # Arrange: elapsed == timeout + grace (75s) — the exact boundary
    now = datetime.now(timezone.utc)
    bot = Bot("b", "b", BotEnvironment.PROD, last_seen=now - timedelta(seconds=75))
    # Act / Assert: strictly greater-than, so the boundary is NOT stale
    assert bot.is_stale(60, 15, now) is False
    # One second past the boundary flips it
    bot.last_seen = now - timedelta(seconds=76)
    assert bot.is_stale(60, 15, now) is True


def test_is_stale_never_seen_is_stale():
    bot = Bot("b", "b", BotEnvironment.PROD, last_seen=None)
    assert bot.is_stale(60, 15, datetime.now(timezone.utc)) is True


def test_hmac_timestamp_skew_boundary(monkeypatch):
    # Arrange: enforce mode with a known secret
    from infrastructure.config import settings
    import infrastructure.secrets as secrets
    monkeypatch.setattr(settings, "HEARTBEAT_AUTH_MODE", "enforce")
    monkeypatch.setattr(secrets, "_shared", "s3cr3t")
    now = int(time.time())
    # Act/Assert: 31s of skew (> MAX_SKEW=30) is rejected as a replay
    stale_ts = str(now - 31)
    with pytest.raises(AuthError):
        verify_signature("b", "prod", stale_ts, _expected_signature("s3cr3t", "b", "prod", stale_ts))
    # A fresh timestamp passes
    fresh = str(now)
    verify_signature("b", "prod", fresh, _expected_signature("s3cr3t", "b", "prod", fresh))


# ═══════════════════════ NEGATIVE / MALICIOUS ═══════════════════════

@pytest.mark.parametrize("bad_id", [
    "'; DROP TABLE bots;--",   # SQL injection
    "<script>alert(1)</script>",  # XSS
    "%%INCIDENTS%%",            # dashboard template-token injection
    "../../etc/passwd",         # path traversal
    "a b",                      # whitespace
    "",                         # empty
    "ñ-unicode",                # non-ASCII
])
def test_malicious_bot_id_rejected(client, bad_id):
    resp = client.post("/heartbeat", json={"bot_id": bad_id, "environment": "prod"})
    assert resp.status_code == 422


def test_template_token_in_name_rejected(client):
    # The %% templating in the dashboard is only safe because input is validated.
    resp = client.post("/heartbeat", json={"bot_id": "b1", "environment": "prod", "name": "%%FLEET_ROWS%%"})
    assert resp.status_code == 422


def test_missing_required_fields_rejected(client):
    assert client.post("/heartbeat", json={"environment": "prod"}).status_code == 422
    assert client.post("/heartbeat", json={"bot_id": "b1"}).status_code == 422
    assert client.post("/heartbeat", json={}).status_code == 422


def test_unknown_environment_rejected(client):
    assert client.post("/heartbeat", json={"bot_id": "b1", "environment": "production"}).status_code == 422


def test_sanitize_rejects_non_dict_payload():
    # Regression: a non-dict `metrics` value used to raise TypeError.
    assert RecordHealthUseCase._sanitize(123) == {}
    assert RecordHealthUseCase._sanitize("string") == {}
    assert RecordHealthUseCase._sanitize(None) == {}
    assert RecordHealthUseCase._sanitize(["list"]) == {}


def test_sanitize_drops_nan_inf_and_bool():
    raw = {
        "inference_latency_p95_ms": float("nan"),
        "llm_error_rate": float("inf"),
        "queue_depth": True,   # bool is a sneaky int
    }
    assert RecordHealthUseCase._sanitize(raw) == {}


def test_ws_non_dict_metrics_does_not_drop_connection(client):
    # Regression: metrics=123 crashed the handler and dropped the agent.
    with client.websocket_connect("/ws/agent?bot_id=hostile-01&environment=prod") as ws:
        ws.send_json({"type": "health", "seq": 1, "metrics": 123})
        assert ws.receive_json()["type"] == "ack"          # survived
        ws.send_json({"type": "heartbeat", "seq": 2})       # still usable
        assert ws.receive_json() == {"type": "ack", "seq": 2}


def test_incident_resolve_never_negative_on_clock_skew():
    # Arrange: recovery timestamp BEFORE the offline timestamp (skew)
    off = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
    inc = Incident("x", "b", BotEnvironment.PROD, offline_at=off)
    # Act
    inc.resolve(off - timedelta(seconds=30))
    # Assert: clamped to 0, never negative
    assert inc.downtime_seconds == 0.0


def test_gauge_clamps_out_of_range_fill():
    assert "width:0%" in _gauge("x", "crit", -500, "v")    # negative metric
    assert "width:100%" in _gauge("x", "crit", 9999, "v")  # absurdly large metric


def test_enforce_rejects_empty_signature(monkeypatch):
    from infrastructure.config import settings
    import infrastructure.secrets as secrets
    monkeypatch.setattr(settings, "HEARTBEAT_AUTH_MODE", "enforce")
    monkeypatch.setattr(secrets, "_shared", "s3cr3t")
    with pytest.raises(AuthError):
        verify_signature("b", "prod", str(int(time.time())), "")  # empty sig


# ════════════════════════════ ROBUSTNESS ════════════════════════════

async def test_throttler_state_does_not_grow_unbounded():
    # Arrange: a repo that reports bots as ONLINE -> every offline event is a
    # glitch (suppressed, never alerted), so its state must be reclaimable.
    import asyncio
    from notifications.manager import NotificationChannel, StatusChangeEvent
    from notifications.throttler import AlertThrottler

    class _OnlineRepo:
        async def find_by_id(self, b, e):
            return Bot(b, b, BotEnvironment.PROD, status=BotStatus.ONLINE)

    class _Sink(NotificationChannel):
        async def send(self, e):
            pass

    t = AlertThrottler([_Sink()], _OnlineRepo(), confirm_seconds=0.01, cooldown_seconds=0.01)

    def _evt(bot_id):
        bot = Bot(bot_id, bot_id, BotEnvironment.PROD, status=BotStatus.OFFLINE)
        return StatusChangeEvent(bot=bot, previous_status=BotStatus.ONLINE,
                                 new_status=BotStatus.OFFLINE, occurred_at=datetime.now(timezone.utc))

    # Act: 300 distinct transient bots flap offline
    for i in range(300):
        await t.send(_evt(f"ghost-{i}"))
    await asyncio.sleep(0.1)        # let confirmations resolve as glitches
    await t.send(_evt("trigger"))   # any new event triggers a prune

    # Assert: state was reclaimed, not leaked 1:1 with bots seen
    assert len(t._state) < 20
