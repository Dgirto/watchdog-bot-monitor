"""
Notification channels (Email + Webhook) — the critical alerting path.
httpx is stubbed; no network. AAA pattern.
"""
import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from domain.entities.bot import Bot, BotEnvironment, BotStatus
from notifications.manager import SendGridEmailChannel, StatusChangeEvent, WebhookChannel


def _event() -> StatusChangeEvent:
    bot = Bot("alpha-bot", "Alpha", BotEnvironment.PROD, status=BotStatus.OFFLINE)
    return StatusChangeEvent(bot=bot, previous_status=BotStatus.ONLINE,
                             new_status=BotStatus.OFFLINE, occurred_at=datetime.now(timezone.utc))


def _fake_httpx(calls, status=200, fail=False):
    """Return a drop-in replacement for httpx.AsyncClient that records posts."""
    class _Resp:
        def raise_for_status(self):
            if status >= 400:
                raise httpx.HTTPStatusError(
                    "error", request=httpx.Request("POST", "http://x"),
                    response=httpx.Response(status))

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if fail:
                raise RuntimeError("network down")
            return _Resp()

    return _Client


# ── WebhookChannel ────────────────────────────────────────────────
def test_webhook_posts_slack_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _fake_httpx(calls))
    asyncio.run(WebhookChannel("https://hooks.slack.com/x").send(_event()))
    assert len(calls) == 1
    assert "text" in calls[0][1]["json"]
    assert "alpha-bot" in calls[0][1]["json"]["text"]


def test_webhook_swallows_network_errors(monkeypatch):
    # A down webhook must never raise into the alerting flow.
    monkeypatch.setattr(httpx, "AsyncClient", _fake_httpx([], fail=True))
    asyncio.run(WebhookChannel("https://x").send(_event()))  # no exception


# ── SendGridEmailChannel ──────────────────────────────────────────
def test_email_posts_once_on_success(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _fake_httpx(calls, status=200))
    channel = SendGridEmailChannel("KEY123", "watchdog@x.com", ["oncall@x.com"])
    asyncio.run(channel.send(_event()))
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert "sendgrid.com" in url
    assert kwargs["headers"]["Authorization"] == "Bearer KEY123"
    assert "oncall@x.com" in str(kwargs["json"])


async def _async_noop(*args, **kwargs):
    return None


def test_email_retries_three_times_then_gives_up(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _fake_httpx(calls, fail=True))
    monkeypatch.setattr(asyncio, "sleep", _async_noop)  # skip backoff waits
    channel = SendGridEmailChannel("KEY", "from@x.com", ["to@x.com"])
    asyncio.run(channel.send(_event()))  # must NOT raise even after all retries
    assert len(calls) == 3  # exhausted retries, alert logged as permanently failed
