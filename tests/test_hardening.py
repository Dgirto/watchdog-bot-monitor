"""Production-hardening: config validation, security headers, WS robustness."""
from infrastructure.config import Settings


# ── Config validation (fail-fast) ─────────────────────────────────
def test_valid_defaults_pass(monkeypatch):
    for k in ("DB_BACKEND", "HEARTBEAT_AUTH_MODE", "DATABASE_URL", "AGENT_SHARED_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert Settings().validate() == []


def test_postgres_requires_database_url(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    errors = Settings().validate()
    assert any("DATABASE_URL" in e for e in errors)


def test_enforce_requires_secret(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_AUTH_MODE", "enforce")
    monkeypatch.delenv("AGENT_SHARED_SECRET", raising=False)
    monkeypatch.delenv("AGENT_SECRETS", raising=False)
    errors = Settings().validate()
    assert any("enforce" in e for e in errors)


def test_invalid_auth_mode_rejected(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_AUTH_MODE", "banana")
    assert any("HEARTBEAT_AUTH_MODE" in e for e in Settings().validate())


def test_sendgrid_requires_sender(monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "SG.x")
    monkeypatch.delenv("ALERT_EMAIL_SENDER", raising=False)
    assert any("ALERT_EMAIL_SENDER" in e for e in Settings().validate())


# ── Docs gating ───────────────────────────────────────────────────
def test_docs_off_in_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("EXPOSE_DOCS", raising=False)
    assert Settings().DOCS_ENABLED is False


def test_docs_on_in_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.delenv("EXPOSE_DOCS", raising=False)
    assert Settings().DOCS_ENABLED is True


def test_docs_explicit_override(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("EXPOSE_DOCS", "true")
    assert Settings().DOCS_ENABLED is True


# ── Security headers ──────────────────────────────────────────────
def test_security_headers_present(client):
    h = client.get("/health").headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"


# ── WebSocket robustness ──────────────────────────────────────────
def test_ws_invalid_json_does_not_drop_connection(client):
    with client.websocket_connect("/ws/agent?bot_id=robust-01&environment=prod") as ws:
        ws.send_text("this is not json {{{")
        assert ws.receive_json()["type"] == "error"
        # The connection survives — a valid heartbeat still works afterwards.
        ws.send_json({"type": "heartbeat", "seq": 1})
        assert ws.receive_json() == {"type": "ack", "seq": 1}
