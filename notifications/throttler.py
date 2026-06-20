"""
notifications/throttler.py
AlertThrottler — turns raw status-change events into *smart* alerts (E-7).

Two protections, both standard for uptime monitors:

  1. Glitch suppression (debounce): on OFFLINE, wait `confirm_seconds` and
     re-check the bot. If it recovered in the meantime, it was a network blip —
     no alert. Only sustained outages page anyone.

  2. Flap/storm protection (cooldown): after an alert fires, stay silent for
     `cooldown_seconds` so a bot bouncing online/offline can't spam recipients.

Recovery alerts are only sent if the matching OFFLINE alert actually went out.

NOTE: state is per-process (in-memory). With multiple HA instances this should
move to Redis (Fase 3); for a single instance it is correct and sufficient.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from domain.entities.bot import BotStatus
from domain.interfaces.repositories import IBotRepository
from infrastructure.time import utcnow
from notifications.manager import NotificationChannel, StatusChangeEvent

logger = logging.getLogger("watchdog.throttler")


@dataclass
class _BotState:
    pending: Optional[asyncio.Task] = None
    cooldown_until: Optional[datetime] = None
    alerted: bool = False


class AlertThrottler(NotificationChannel):
    """Decorates a set of real alert channels with debounce + cooldown logic."""

    def __init__(
        self,
        channels: List[NotificationChannel],
        bot_repo: IBotRepository,
        confirm_seconds: int = 90,
        cooldown_seconds: int = 300,
    ):
        self._channels = channels
        self._bots = bot_repo
        self._confirm_seconds = confirm_seconds
        self._cooldown_seconds = cooldown_seconds
        self._state: dict[tuple, _BotState] = {}

    async def send(self, event: StatusChangeEvent) -> None:
        self._prune()
        key = (event.bot.bot_id, event.bot.environment.value)
        state = self._state.setdefault(key, _BotState())

        if event.new_status == BotStatus.OFFLINE:
            await self._on_offline(key, state, event)
        else:
            await self._on_online(state, event)

    def _prune(self) -> None:
        """Drop fully-idle bots so per-process state can't grow unbounded
        (e.g. transient bot ids that flapped once). Keeps only bots with a
        pending confirmation, an unacknowledged alert, or an active cooldown."""
        now = utcnow()
        idle = [
            k for k, s in self._state.items()
            if (s.pending is None or s.pending.done())
            and not s.alerted
            and (s.cooldown_until is None or now >= s.cooldown_until)
        ]
        for k in idle:
            self._state.pop(k, None)

    # ── OFFLINE: debounce, then confirm before alerting ──────────────
    async def _on_offline(self, key: tuple, state: _BotState, event: StatusChangeEvent) -> None:
        now = utcnow()
        if state.cooldown_until and now < state.cooldown_until:
            logger.info("Alert suppressed (cooldown) | bot=%s", key[0])
            return
        if state.pending and not state.pending.done():
            return  # confirmation already scheduled
        state.pending = asyncio.create_task(self._confirm(key, state, event))

    async def _confirm(self, key: tuple, state: _BotState, event: StatusChangeEvent) -> None:
        try:
            await asyncio.sleep(self._confirm_seconds)
            bot = await self._bots.find_by_id(key[0], key[1])
            if bot and bot.status == BotStatus.OFFLINE:
                await self._forward(event)
                state.alerted = True
                state.cooldown_until = utcnow() + timedelta(seconds=self._cooldown_seconds)
            else:
                logger.info("Glitch suppressed | bot=%s recovered before confirmation", key[0])
        except asyncio.CancelledError:
            pass
        finally:
            state.pending = None

    # ── ONLINE: cancel pending, send recovery only if we alerted ─────
    async def _on_online(self, state: _BotState, event: StatusChangeEvent) -> None:
        if state.pending and not state.pending.done():
            state.pending.cancel()
            state.pending = None
        if state.alerted:
            await self._forward(event)
            state.alerted = False
            state.cooldown_until = None

    async def _forward(self, event: StatusChangeEvent) -> None:
        for channel in self._channels:
            try:
                await channel.send(event)
            except Exception as exc:  # noqa: BLE001
                logger.error("Throttled channel %s raised: %s", type(channel).__name__, exc)
