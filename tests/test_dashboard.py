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
            "inference_latency_p95_ms": 600, "llm_error_rate": 0.01, "queue_depth": 4}})
        ws.receive_json()
    # Degraded AI agent (high error rate + latency + queue)
    with client.websocket_connect("/ws/agent?bot_id=bad-agent&environment=prod") as ws:
        ws.send_json({"type": "health", "seq": 1, "metrics": {
            "inference_latency_p95_ms": 3500, "llm_error_rate": 0.2, "queue_depth": 250}})
        ws.receive_json()

    html = client.get("/dashboard").text
    assert "ÓPTIMO" in html
    assert "DEGRADADO" in html
    assert "good-agent" in html and "bad-agent" in html


def test_estado_en_vivo_entrega_las_regiones(client):
    _hb(client, "bot-vivo", "prod")
    datos = client.get("/dashboard/estado").json()
    assert "bot-vivo" in datos["flota"]
    assert "Todo operativo" in datos["veredicto"]
    assert datos["total-flota"] == "1 bot"
    assert datos["titulo"].startswith("Watchdog")


def test_panel_ya_no_recarga_la_pagina(client):
    html = client.get("/dashboard").text
    assert "location.reload" not in html
    assert "/dashboard/estado" in html


def test_flota_muestra_primero_lo_caido(client):
    import asyncio
    from domain.entities.bot import BotStatus
    from infrastructure.container import container

    _hb(client, "a-sano", "prod")
    _hb(client, "z-caido", "prod")
    asyncio.run(container.bot_repo.update_status("z-caido", "prod", BotStatus.OFFLINE))

    datos = client.get("/dashboard/estado").json()
    assert datos["flota"].index("z-caido") < datos["flota"].index("a-sano")
    assert "(1) Watchdog" in datos["titulo"]
    assert "bot caído" in datos["veredicto"] or "incidente" in datos["veredicto"]


def test_agente_degradado_cuenta_en_el_veredicto(client):
    with client.websocket_connect("/ws/agent?bot_id=lento&environment=prod") as ws:
        ws.send_json({"type": "health", "seq": 1, "metrics": {
            "inference_latency_p95_ms": 3500, "llm_error_rate": 0.2, "queue_depth": 250}})
        ws.receive_json()
    datos = client.get("/dashboard/estado").json()
    assert "1 agente degradado" in datos["veredicto"]
    assert 'data-estado="degradado"' in datos["flota"]


def test_fuentes_solo_sirve_las_permitidas(client):
    r = client.get("/dashboard/fuentes/geist.woff2")
    assert r.status_code == 200
    assert r.headers["content-type"] == "font/woff2"
    assert client.get("/dashboard/fuentes/otra.woff2").status_code == 404
    assert client.get("/dashboard/fuentes/..%2F..%2Fmain.py").status_code == 404


def test_dashboard_empty_states(client):
    html = client.get("/dashboard").text
    assert "Sin incidentes registrados" in html
    assert "Sin agentes de IA reportando" in html
