"""
QA round 2 — limit clamping, tenant separation, idempotency, alert isolation.
AAA pattern.
"""
from datetime import datetime, timezone

from domain.entities.bot import Bot, BotEnvironment, BotStatus
from notifications.manager import NotificationChannel, NotificationManager, StatusChangeEvent


def _event() -> StatusChangeEvent:
    bot = Bot("b", "b", BotEnvironment.PROD, status=BotStatus.OFFLINE)
    return StatusChangeEvent(bot=bot, previous_status=BotStatus.ONLINE,
                             new_status=BotStatus.OFFLINE, occurred_at=datetime.now(timezone.utc))


# ── Limit clamping (resource-exhaustion guard) ────────────────────
def test_health_limit_negative_is_clamped_not_unbounded(client):
    # Arrange: store 3 metric rows
    with client.websocket_connect("/ws/agent?bot_id=lim&environment=prod") as ws:
        for i in range(3):
            ws.send_json({"type": "health", "seq": i, "metrics": {"queue_depth": i}})
            ws.receive_json()
    # Act: a negative limit (SQLite treats LIMIT -1 as "no limit")
    rows = client.get("/agents/lim/health?environment=prod&limit=-1").json()
    # Assert: clamped to 1, NOT all rows
    assert len(rows) == 1


def test_health_limit_huge_is_capped(client):
    with client.websocket_connect("/ws/agent?bot_id=lim2&environment=prod") as ws:
        for i in range(3):
            ws.send_json({"type": "health", "seq": i, "metrics": {"queue_depth": i}})
            ws.receive_json()
    rows = client.get("/agents/lim2/health?environment=prod&limit=999999").json()
    assert len(rows) == 3  # all real rows, but never more than the 200 cap


def test_health_limit_non_int_rejected(client):
    assert client.get("/agents/x/health?environment=prod&limit=abc").status_code == 422


# ── Tenant / environment separation ───────────────────────────────
def test_same_bot_id_in_two_environments_are_distinct(client):
    # Arrange / Act
    client.post("/heartbeat", json={"bot_id": "multi", "environment": "prod"})
    client.post("/heartbeat", json={"bot_id": "multi", "environment": "dev"})
    # Assert: composite PK (bot_id, environment) keeps them separate
    bots = [b for b in client.get("/status").json()["bots"] if b["bot_id"] == "multi"]
    assert {b["environment"] for b in bots} == {"prod", "dev"}


# ── Idempotent registration ───────────────────────────────────────
def test_duplicate_registration_is_idempotent(client):
    first = client.post("/heartbeat", json={"bot_id": "dup", "environment": "prod"}).json()
    second = client.post("/heartbeat", json={"bot_id": "dup", "environment": "prod"}).json()
    assert first["registered"] is True
    assert second["registered"] is False  # already known — no duplicate row
    assert client.get("/status").json()["total"] == 1


# ── Notification isolation ────────────────────────────────────────
async def test_failing_channel_does_not_block_others():
    # Arrange: a channel that throws, in front of a healthy one
    class _Boom(NotificationChannel):
        async def send(self, e):
            raise RuntimeError("boom")

    class _Good(NotificationChannel):
        def __init__(self):
            self.hits = 0

        async def send(self, e):
            self.hits += 1

    good = _Good()
    manager = NotificationManager([_Boom(), good])
    # Act
    await manager.notify(_event())
    # Assert: the exception was contained; the healthy channel still fired
    assert good.hits == 1
