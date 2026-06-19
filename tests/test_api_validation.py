"""HTTP API: input validation (S-3) and basic heartbeat flow."""


def test_rejects_xss_name(client):
    r = client.post("/heartbeat", json={
        "bot_id": "bot-1", "environment": "prod", "name": "<script>alert(1)</script>",
    })
    assert r.status_code == 422


def test_rejects_bad_bot_id(client):
    r = client.post("/heartbeat", json={"bot_id": "../etc/passwd", "environment": "prod"})
    assert r.status_code == 422


def test_rejects_invalid_environment(client):
    r = client.post("/heartbeat", json={"bot_id": "bot-1", "environment": "nope"})
    assert r.status_code == 422


def test_valid_heartbeat_registers_online(client):
    r = client.post("/heartbeat", json={
        "bot_id": "price-tracker-01", "environment": "prod", "name": "Price Tracker",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "online"
    assert body["registered"] is True

    status = client.get("/status").json()
    assert status["total"] == 1
    assert status["online"] == 1


def test_dashboard_renders(client):
    client.post("/heartbeat", json={"bot_id": "bot-1", "environment": "prod"})
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Watchdog" in r.text


def test_enforce_mode_rejects_unsigned_http(client, enforce_auth):
    r = client.post("/heartbeat", json={"bot_id": "bot-1", "environment": "prod"})
    assert r.status_code == 401
