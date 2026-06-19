"""AlertThrottler: glitch debounce + flap cooldown (E-7)."""
from datetime import datetime, timezone

import pytest

from domain.entities.bot import Bot, BotEnvironment, BotStatus
from notifications.manager import NotificationChannel, StatusChangeEvent
from notifications.throttler import AlertThrottler


class FakeRepo:
    def __init__(self, status=BotStatus.OFFLINE):
        self.status = status

    async def find_by_id(self, bot_id, env):
        return Bot(bot_id=bot_id, name=bot_id, environment=BotEnvironment.PROD, status=self.status)


class Spy(NotificationChannel):
    def __init__(self):
        self.sent = []

    async def send(self, e):
        self.sent.append(e.new_status.value)


def _evt(status):
    bot = Bot(bot_id="b1", name="b1", environment=BotEnvironment.PROD, status=status)
    return StatusChangeEvent(bot=bot, previous_status=BotStatus.ONLINE,
                             new_status=status, occurred_at=datetime.now(timezone.utc))


async def test_glitch_is_suppressed():
    repo, spy = FakeRepo(), Spy()
    t = AlertThrottler([spy], repo, confirm_seconds=0.3, cooldown_seconds=1)

    await t.send(_evt(BotStatus.OFFLINE))   # schedules confirmation
    import asyncio
    await asyncio.sleep(0.1)
    repo.status = BotStatus.ONLINE          # recovered before confirmation
    await t.send(_evt(BotStatus.ONLINE))    # cancels pending
    await asyncio.sleep(0.4)

    assert spy.sent == []                    # never alerted


async def test_sustained_outage_alerts_then_recovers():
    import asyncio
    repo, spy = FakeRepo(), Spy()
    t = AlertThrottler([spy], repo, confirm_seconds=0.2, cooldown_seconds=1)

    await t.send(_evt(BotStatus.OFFLINE))
    await asyncio.sleep(0.4)                  # stays offline through window
    assert spy.sent == ["offline"]

    repo.status = BotStatus.ONLINE
    await t.send(_evt(BotStatus.ONLINE))
    assert spy.sent == ["offline", "online"]


async def test_no_recovery_alert_without_prior_offline_alert():
    repo, spy = FakeRepo(), Spy()
    t = AlertThrottler([spy], repo, confirm_seconds=10, cooldown_seconds=1)
    # Recovery with nothing pending/alerted -> silence
    await t.send(_evt(BotStatus.ONLINE))
    assert spy.sent == []
