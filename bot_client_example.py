"""
bot_client_example.py
─────────────────────
Drop this file into any Python bot to add heartbeat support.
It runs as a background coroutine — zero impact on the bot's main logic.

Usage:
    from bot_client_example import HeartbeatClient

    client = HeartbeatClient(
        bot_id="price-tracker-01",
        environment="prod",           # prod | staging | dev
        name="Price Tracker Bot",     # display name in the dashboard
        watchdog_url="http://watchdog-host:8000",
        interval=30,                  # seconds between heartbeats
    )

    # Inside your async main:
    async with client:
        await your_bot_logic()
"""
import asyncio
import hashlib
import hmac
import logging
import time

import httpx

logger = logging.getLogger("bot.heartbeat")


class HeartbeatClient:
    """
    Sends a POST /heartbeat to the Watchdog service at a regular interval.
    Designed to be used as an async context manager.
    """

    def __init__(
        self,
        bot_id: str,
        environment: str,
        watchdog_url: str = "http://localhost:8000",
        name: str | None = None,
        interval: int = 30,
        timeout: int = 5,
        secret: str | None = None,
    ):
        self.bot_id = bot_id
        self.environment = environment
        self.watchdog_url = watchdog_url.rstrip("/")
        self.name = name or bot_id
        self.interval = interval
        self.timeout = timeout
        self.secret = secret  # if set, heartbeats are HMAC-signed
        self._task: asyncio.Task | None = None

    def _auth_headers(self) -> dict:
        """Build HMAC signature headers. Empty dict if no secret (unsigned)."""
        if not self.secret:
            return {}
        ts = str(int(time.time()))
        msg = f"{self.bot_id}|{self.environment}|{ts}".encode()
        sig = hmac.new(self.secret.encode(), msg, hashlib.sha256).hexdigest()
        return {"X-Timestamp": ts, "X-Signature": sig}

    async def _send_heartbeat(self, client: httpx.AsyncClient) -> None:
        payload = {
            "bot_id": self.bot_id,
            "environment": self.environment,
            "name": self.name,
        }
        try:
            response = await client.post(
                f"{self.watchdog_url}/heartbeat",
                json=payload,
                headers=self._auth_headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            logger.debug("Heartbeat OK → %s", response.json().get("status"))
        except httpx.HTTPStatusError as e:
            logger.warning("Heartbeat rejected (HTTP %d): %s", e.response.status_code, e.response.text)
        except httpx.RequestError as e:
            logger.warning("Heartbeat network error: %s", e)

    async def _loop(self) -> None:
        async with httpx.AsyncClient() as client:
            logger.info(
                "Heartbeat loop started — bot_id=%s env=%s interval=%ds",
                self.bot_id, self.environment, self.interval,
            )
            while True:
                await self._send_heartbeat(client)
                await asyncio.sleep(self.interval)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return  # already running
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("Heartbeat loop stopped for bot_id=%s", self.bot_id)

    # Async context manager support
    async def __aenter__(self) -> "HeartbeatClient":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()


# ──────────────────────────────────────────────────────────────────
# WebSocket client — real-time transport with auto-reconnect
# Requires: pip install websockets
# ──────────────────────────────────────────────────────────────────
class WSHeartbeatClient:
    """
    Persistent WebSocket connection to the Watchdog. Sends signed heartbeats and
    optional AI health metrics. Reconnects automatically with exponential backoff
    + jitter (avoids a thundering herd when the server restarts).

    `metrics_provider` is an optional callable returning a dict of health metrics
    (inference_latency_p95_ms, tokens_per_sec, llm_error_rate, session_cost_usd,
    queue_depth) sent on each tick.
    """

    def __init__(
        self,
        bot_id: str,
        environment: str,
        watchdog_url: str = "ws://localhost:8000",
        secret: str | None = None,
        interval: int = 30,
        metrics_provider=None,
    ):
        self.bot_id = bot_id
        self.environment = environment
        self.base = watchdog_url.rstrip("/")
        self.secret = secret
        self.interval = interval
        self.metrics_provider = metrics_provider

    def _url(self) -> str:
        ts = str(int(time.time()))
        q = f"bot_id={self.bot_id}&environment={self.environment}"
        if self.secret:
            msg = f"{self.bot_id}|{self.environment}|{ts}".encode()
            sig = hmac.new(self.secret.encode(), msg, hashlib.sha256).hexdigest()
            q += f"&ts={ts}&sig={sig}"
        return f"{self.base}/ws/agent?{q}"

    async def run(self) -> None:
        import json
        import random
        import websockets  # lazy import

        backoff = 1
        seq = 0
        while True:
            try:
                async with websockets.connect(self._url()) as ws:
                    logger.info("WS connected → %s", self.bot_id)
                    backoff = 1  # reset after a successful connect
                    while True:
                        seq += 1
                        if self.metrics_provider:
                            await ws.send(json.dumps(
                                {"type": "health", "seq": seq, "metrics": self.metrics_provider()}
                            ))
                        else:
                            await ws.send(json.dumps({"type": "heartbeat", "seq": seq}))
                        await ws.recv()  # ack
                        await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                wait = min(backoff, 30) + random.uniform(0, 1)  # jitter
                logger.warning("WS disconnected (%s); reconnecting in %.1fs", exc, wait)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 30)


# ──────────────────────────────────────────────────────────────────
# Minimal demo — run with: python bot_client_example.py
# ──────────────────────────────────────────────────────────────────
async def _demo():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )

    client = HeartbeatClient(
        bot_id="demo-bot-01",
        environment="dev",
        name="Demo Bot",
        watchdog_url="http://localhost:8000",
        interval=15,
    )

    async with client:
        logger.info("Bot is running… (Ctrl+C to stop)")
        try:
            # Simulate the bot doing its actual work
            while True:
                logger.info("Bot doing work…")
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(_demo())
