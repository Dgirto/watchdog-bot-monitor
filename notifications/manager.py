"""
notifications/manager.py
NotificationManager — plug in any channel without touching business logic.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List
from domain.entities.bot import Bot, BotStatus
import logging

logger = logging.getLogger("watchdog.notifications")


@dataclass
class StatusChangeEvent:
    bot: Bot
    previous_status: BotStatus
    new_status: BotStatus
    occurred_at: datetime


class NotificationChannel(ABC):
    """Base class for every notification channel."""

    @abstractmethod
    async def send(self, event: StatusChangeEvent) -> None: ...


class LogChannel(NotificationChannel):
    """Default channel: structured log output. Always active."""

    async def send(self, event: StatusChangeEvent) -> None:
        emoji = "🔴" if event.new_status == BotStatus.OFFLINE else "🟢"
        logger.warning(
            "%s Bot status changed | bot_id=%s env=%s %s → %s at %s",
            emoji,
            event.bot.bot_id,
            event.bot.environment,
            event.previous_status.value,
            event.new_status.value,
            event.occurred_at.isoformat(),
        )


class WebhookChannel(NotificationChannel):
    """
    Example: POST to a Slack/Discord/Teams webhook.
    Activate by passing a webhook URL in config.
    """

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, event: StatusChangeEvent) -> None:
        import httpx  # lazy import — only needed if this channel is active
        emoji = "🔴" if event.new_status == BotStatus.OFFLINE else "🟢"
        payload = {
            "text": (
                f"{emoji} *Bot Alert* — `{event.bot.bot_id}` "
                f"({event.bot.environment}) changed to *{event.new_status.value.upper()}* "
                f"at {event.occurred_at.isoformat()}"
            )
        }
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                await client.post(self.webhook_url, json=payload)
            except Exception as exc:  # noqa: BLE001
                logger.error("WebhookChannel failed: %s", exc)


class EmailChannel(NotificationChannel):
    """
    Stub — wire up smtplib / SendGrid / SES here.
    The interface is the contract; the implementation is your choice.
    """

    def __init__(self, recipients: List[str]):
        self.recipients = recipients

    async def send(self, event: StatusChangeEvent) -> None:
        # TODO: implement with your preferred email provider
        logger.debug(
            "EmailChannel (stub) → would email %s about %s",
            self.recipients,
            event.bot.bot_id,
        )


class NotificationManager:
    """
    Orchestrates all registered channels.
    Add channels at startup; they're called on every status-change event.
    """

    def __init__(self, channels: List[NotificationChannel] | None = None):
        self._channels: List[NotificationChannel] = channels or [LogChannel()]

    def add_channel(self, channel: NotificationChannel) -> None:
        self._channels.append(channel)

    async def notify(self, event: StatusChangeEvent) -> None:
        for channel in self._channels:
            try:
                await channel.send(event)
            except Exception as exc:  # noqa: BLE001
                logger.error("Channel %s raised: %s", type(channel).__name__, exc)
