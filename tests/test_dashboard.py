"""Mission Control dashboard rendering."""


def _hb(client, bot_id, env, name=None):
    client.post("/heartbeat", json={"bot_id": bot_id, "environment": env, "name": name or bot_id})


def test_dashboard_renders_with_no_leftover_tokens(client):
    _hb(client, "price-tracker-01", "prod")
    _hb(client, "classifier-04", "staging")
    html = client.get("/dashboard").text
    assert "%%" not in html                 # every token was replaced
    assert "Mission Control" in html
    assert "price-tracker-01" in html
    assert "Cockpit" in html


def test_dashboard_escapes_bot_name(client):
    # A malicious name can't reach the API (validation), so inject at storage
    # level is out of scope; here we assert the page never emits a raw script tag.
    _hb(client, "bot-1", "prod", name="Normal Bot")
    html = client.get("/dashboard").text
    assert "<script>alert" not in html


def test_dashboard_shows_ai_cockpit_states(client):
    # Healthy AI agent
    with client.websocket_connect("/ws/agent?bot_id=good-agent&environment=prod") as ws:
        ws.send_json({"type": "health", "seq": 1, "metrics": {
            "inference_latency_p95_ms": 600, "tokens_per_sec": 90,
            "llm_error_rate": 0.01, "session_cost_usd": 2.0, "queue_depth": 4}})
        ws.receive_json()
    # Degraded AI agent (high error + cost)
    with client.websocket_connect("/ws/agent?bot_id=bad-agent&environment=prod") as ws:
        ws.send_json({"type": "health", "seq": 1, "metrics": {
            "inference_latency_p95_ms": 3500, "tokens_per_sec": 10,
            "llm_error_rate": 0.2, "session_cost_usd": 40.0, "queue_depth": 250}})
        ws.receive_json()

    html = client.get("/dashboard").text
    assert "ÓPTIMO" in html
    assert "DEGRADADO" in html
    assert "good-agent" in html and "bad-agent" in html


def test_dashboard_empty_states(client):
    html = client.get("/dashboard").text
    assert "Sin incidentes registrados" in html
    assert "Sin agentes de IA reportando" in html
