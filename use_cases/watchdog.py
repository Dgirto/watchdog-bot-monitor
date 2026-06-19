"""
use_cases/heartbeat.py  — process an incoming heartbeat from a bot
use_cases/watchdog.py   — periodic stale-bot checker
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import uuid

from domain.entities.bot import Bot, BotEnvironment, BotStatus, Incident
from domain.interfaces.repositories import IBotRepository, IIncidentRepository
from infrastructure.time import utcnow
from notifications.manager import NotificationManager, StatusChangeEvent


# ─────────────────────────── Heartbeat ───────────────────────────

@dataclass
class HeartbeatRequest:
    bot_id: str
    environment: str
    name: Optional[str] = None   # optional display name on first registration


@dataclass
class HeartbeatResponse:
    bot_id: str
    environment: str
    status: str
    registered: bool             # True if this was a first-time registration
    timestamp: datetime


class ProcessHeartbeatUseCase:
    """
    Receives a heartbeat signal from a bot:
    1. Upserts the bot record (registers if new).
    2. Marks it Online.
    3. Closes any open incident and fires a recovery notification.
    """

    def __init__(
        self,
        bot_repo: IBotRepository,
        incident_repo: IIncidentRepository,
        notification_manager: NotificationManager,
    ):
        self._bots = bot_repo
        self._incidents = incident_repo
        self._notifications = notification_manager

    async def execute(self, request: HeartbeatRequest) -> HeartbeatResponse:
        now = utcnow()
        env = BotEnvironment(request.environment)

        existing = await self._bots.find_by_id(request.bot_id, request.environment)
        previous_status = existing.status if existing else BotStatus.UNKNOWN
        registered = existing is None

        bot = existing or Bot(
            bot_id=request.bot_id,
            name=request.name or request.bot_id,
            environment=env,
        )
        bot.record_heartbeat(now)

        await self._bots.upsert(bot)

        # Close active incident if any
        if previous_status == BotStatus.OFFLINE:
            incident = await self._incidents.close_incident(
                request.bot_id, request.environment, now
            )
            if incident:
                event = StatusChangeEvent(
                    bot=bot,
                    previous_status=BotStatus.OFFLINE,
                    new_status=BotStatus.ONLINE,
                    occurred_at=now,
                )
                await self._notifications.notify(event)

        return HeartbeatResponse(
            bot_id=bot.bot_id,
            environment=bot.environment.value,
            status=bot.status.value,
            registered=registered,
            timestamp=now,
        )


# ─────────────────────────── Watchdog ────────────────────────────

class RunWatchdogUseCase:
    """
    Background sweep:
    1. Loads all bots.
    2. Any bot whose last_seen is beyond (timeout + grace) is marked Offline.
    3. Opens an incident and fires an alert — but only on the *transition*.
    """

    def __init__(
        self,
        bot_repo: IBotRepository,
        incident_repo: IIncidentRepository,
        notification_manager: NotificationManager,
        timeout_seconds: int = 60,
        grace_seconds: int = 15,
    ):
        self._bots = bot_repo
        self._incidents = incident_repo
        self._notifications = notification_manager
        self._timeout = timeout_seconds
        self._grace = grace_seconds

    async def execute(self) -> int:
        """Returns the number of bots newly marked as offline."""
        now = utcnow()
        bots = await self._bots.find_all()
        newly_offline = 0

        for bot in bots:
            if bot.status != BotStatus.OFFLINE and bot.is_stale(self._timeout, self._grace, now):
                previous_status = bot.status
                bot.mark_offline()
                await self._bots.update_status(bot.bot_id, bot.environment.value, BotStatus.OFFLINE.value)

                # Avoid duplicate open incidents
                active = await self._incidents.find_active_incident(bot.bot_id, bot.environment.value)
                if not active:
                    incident = Incident(
                        incident_id=str(uuid.uuid4()),
                        bot_id=bot.bot_id,
                        environment=bot.environment,
                        offline_at=now,
                    )
                    await self._incidents.open_incident(incident)

                    event = StatusChangeEvent(
                        bot=bot,
                        previous_status=previous_status,
                        new_status=BotStatus.OFFLINE,
                        occurred_at=now,
                    )
                    await self._notifications.notify(event)
                    newly_offline += 1

        return newly_offline
