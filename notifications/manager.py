"""
notifications/manager.py
NotificationManager — plug in any channel without touching business logic.
"""
import asyncio
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


class SendGridEmailChannel(NotificationChannel):
    """
    Email alerts via the SendGrid v3 HTTP API (uses httpx — no new dependency).
    Retries with backoff so a transient provider hiccup doesn't lose an alert (S-8).

    For AWS SES, swap the URL/auth/payload — the NotificationChannel contract
    is unchanged.
    """

    _ENDPOINT = "https://api.sendgrid.com/v3/mail/send"
    _MAX_RETRIES = 3

    def __init__(self, api_key: str, sender: str, recipients: List[str]):
        self.api_key = api_key
        self.sender = sender
        self.recipients = recipients

    def _build_payload(self, event: StatusChangeEvent) -> dict:
        status = event.new_status.value.upper()
        subject = f"[Watchdog] {event.bot.bot_id} → {status} ({event.bot.environment.value})"
        body = (
            f"Agent:       {event.bot.bot_id}\n"
            f"Environment: {event.bot.environment.value}\n"
            f"Change:      {event.previous_status.value} → {event.new_status.value}\n"
            f"Occurred at: {event.occurred_at.isoformat()} (UTC)\n"
        )
        return {
            "personalizations": [{"to": [{"email": r} for r in self.recipients]}],
            "from": {"email": self.sender},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }

    async def send(self, event: StatusChangeEvent) -> None:
        import httpx  # lazy import — only needed if this channel is active

        payload = self._build_payload(event)
        headers = {"Authorization": f"Bearer {self.api_key}"}

        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(self._ENDPOINT, json=payload, headers=headers)
                    resp.raise_for_status()
                    logger.info("Email alert sent for %s (%s)", event.bot.bot_id, event.new_status.value)
                    return
            except Exception as exc:  # noqa: BLE001
                wait = 2 ** (attempt - 1)
                logger.warning("Email attempt %d/%d failed: %s", attempt, self._MAX_RETRIES, exc)
                if attempt < self._MAX_RETRIES:
                    await asyncio.sleep(wait)
        logger.error("Email alert PERMANENTLY failed for %s — manual follow-up needed", event.bot.bot_id)


# Backwards-compat alias: older imports of EmailChannel still resolve.
EmailChannel = SendGridEmailChannel


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
