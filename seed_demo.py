"""
seed_demo.py — populate a running Watchdog with a realistic fleet for a demo.

Run the server first:
    python -m uvicorn main:app --port 8000
Then in another terminal:
    python seed_demo.py

Sends HTTP heartbeats for plain bots and WebSocket `health` messages for AI
agents (one healthy, one degraded), so /dashboard shows a lively fleet and the
AI cockpit with different states.
"""
import asyncio
import json
import os

import httpx
import websockets

# Override with WATCHDOG_URL (e.g. http://localhost:8200)
BASE_HTTP = os.getenv("WATCHDOG_URL", "http://localhost:8000").rstrip("/")
BASE_WS = BASE_HTTP.replace("http://", "ws://").replace("https://", "wss://")

# Plain bots (HTTP heartbeat) — (bot_id, environment, name)
PLAIN_BOTS = [
    ("price-tracker-01", "prod", "Price Tracker Bot"),
    ("scraper-02", "prod", "News Scraper"),
    ("price-tracker-dev", "dev", "Price Tracker Bot"),
]

# AI agents (WS health) — (bot_id, environment, metrics)
AI_AGENTS = [
    ("classifier-04", "staging", {           # healthy
        "inference_latency_p95_ms": 410, "llm_error_rate": 0.003, "queue_depth": 3,
    }),
    ("summarizer-ai-03", "prod", {           # degraded: high error + latency + queue
        "inference_latency_p95_ms": 3400, "llm_error_rate": 0.18, "queue_depth": 240,
    }),
]


async def send_plain():
    async with httpx.AsyncClient() as c:
        for bot_id, env, name in PLAIN_BOTS:
            r = await c.post(f"{BASE_HTTP}/heartbeat",
                             json={"bot_id": bot_id, "environment": env, "name": name})
            print(f"  heartbeat {bot_id:<18} {env:<8} -> {r.status_code}")


async def send_ai():
    for bot_id, env, metrics in AI_AGENTS:
        url = f"{BASE_WS}/ws/agent?bot_id={bot_id}&environment={env}"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"type": "health", "seq": 1, "metrics": metrics}))
            await ws.recv()  # ack
            print(f"  ws health {bot_id:<18} {env:<8} -> ok")


async def main():
    print("Seeding fleet...")
    await send_plain()
    await send_ai()
    print("\nDone. Open http://localhost:8000/dashboard")


if __name__ == "__main__":
    asyncio.run(main())
