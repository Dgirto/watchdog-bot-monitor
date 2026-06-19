"""Watchdog sweep: N+1 elimination (E-2), idempotency, recovery."""
from datetime import timedelta

from domain.entities.bot import Bot, BotEnvironment, BotStatus
from infrastructure.time import utcnow
from notifications.manager import NotificationManager, NotificationChannel
from use_cases.watchdog import (
    ProcessHeartbeatUseCase, RunWatchdogUseCase, HeartbeatRequest,
)


class _Spy(NotificationChannel):
    def __init__(self):
        self.events = []

    async def send(self, e):
        self.events.append((e.bot.bot_id, e.new_status.value))


async def _seed_stale_bots(repo, n):
    bots = repo.SqliteBotRepository()
    old = utcnow() - timedelta(seconds=10_000)
    for i in range(n):
        await bots.upsert(Bot(
            bot_id=f"bot-{i}", name=f"bot-{i}",
            environment=BotEnvironment.PROD, status=BotStatus.ONLINE, last_seen=old,
        ))
    return bots


async def test_sweep_eliminates_n_plus_one(sqlite_repo):
    await sqlite_repo.init_db()

    class Counting(sqlite_repo.SqliteIncidentRepository):
        per_bot = 0
        bulk = 0

        async def find_active_incident(self, *a, **k):
            type(self).per_bot += 1
            return await super().find_active_incident(*a, **k)

        async def find_active_bot_keys(self):
            type(self).bulk += 1
            return await super().find_active_bot_keys()

    bots = await _seed_stale_bots(sqlite_repo, 5)
    inc = Counting()
    nm = NotificationManager([_Spy()])
    wd = RunWatchdogUseCase(bots, inc, nm, timeout_seconds=60, grace_seconds=15)

    n = await wd.execute()
    assert n == 5
    assert Counting.per_bot == 0      # no per-bot incident lookups
    assert Counting.bulk == 1         # exactly one bulk query


async def test_sweep_is_idempotent(sqlite_repo):
    await sqlite_repo.init_db()
    bots = await _seed_stale_bots(sqlite_repo, 3)
    inc = sqlite_repo.SqliteIncidentRepository()
    spy = _Spy()
    wd = RunWatchdogUseCase(bots, inc, NotificationManager([spy]),
                            timeout_seconds=60, grace_seconds=15)

    assert await wd.execute() == 3
    assert await wd.execute() == 0    # already offline, no duplicate incidents
    assert len(spy.events) == 3


async def test_heartbeat_closes_incident(sqlite_repo):
    await sqlite_repo.init_db()
    bots = await _seed_stale_bots(sqlite_repo, 1)
    inc = sqlite_repo.SqliteIncidentRepository()
    nm = NotificationManager([_Spy()])
    wd = RunWatchdogUseCase(bots, inc, nm, timeout_seconds=60, grace_seconds=15)
    await wd.execute()
    assert ("bot-0", "prod") in await inc.find_active_bot_keys()

    ph = ProcessHeartbeatUseCase(bots, inc, nm)
    await ph.execute(HeartbeatRequest(bot_id="bot-0", environment="prod"))
    assert ("bot-0", "prod") not in await inc.find_active_bot_keys()
