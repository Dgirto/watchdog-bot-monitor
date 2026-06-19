"""WebSocket real-time transport (/ws/agent)."""
import time

import pytest

from adapters.controllers.auth import _expected_signature


def test_ws_rejects_invalid_identity(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/agent?bot_id=../bad&environment=prod") as ws:
            ws.receive()


def test_ws_heartbeat_acks_and_marks_online(client):
    with client.websocket_connect("/ws/agent?bot_id=agent-01&environment=prod") as ws:
        ws.send_json({"type": "heartbeat", "seq": 1})
        assert ws.receive_json() == {"type": "ack", "seq": 1}
    assert client.get("/status").json()["online"] == 1


def test_ws_health_metrics_stored_and_sanitized(client):
    with client.websocket_connect("/ws/agent?bot_id=agent-01&environment=prod") as ws:
        ws.send_json({"type": "health", "seq": 2, "metrics": {
            "inference_latency_p95_ms": 820.0,
            "llm_error_rate": 0.12,
            "queue_depth": 5,
            "evil": "<script>",      # must be dropped
        }})
        assert ws.receive_json()["type"] == "ack"

    rows = client.get("/agents/agent-01/health?environment=prod").json()
    assert len(rows) == 1
    assert rows[0]["inference_latency_p95_ms"] == 820.0
    assert rows[0]["queue_depth"] == 5
    assert "evil" not in rows[0]
    assert "tokens_per_sec" not in rows[0]


def test_ws_unknown_message_returns_error(client):
    with client.websocket_connect("/ws/agent?bot_id=agent-01&environment=prod") as ws:
        ws.send_json({"type": "garbage"})
        assert ws.receive_json()["type"] == "error"


def test_ws_enforce_rejects_unsigned(client, enforce_auth):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/agent?bot_id=a&environment=prod") as ws:
            ws.receive()


def test_ws_enforce_accepts_signed(client, enforce_auth):
    ts = str(int(time.time()))
    sig = _expected_signature(enforce_auth, "a", "prod", ts)
    url = f"/ws/agent?bot_id=a&environment=prod&ts={ts}&sig={sig}"
    with client.websocket_connect(url) as ws:
        ws.send_json({"type": "heartbeat", "seq": 1})
        assert ws.receive_json()["type"] == "ack"
