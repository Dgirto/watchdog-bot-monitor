"""
live_agent.py — a REAL persistent WebSocket agent for testing "online" status.

Unlike seed_demo.py (which sends one message and disconnects), this keeps the
connection open and sends a heartbeat every few seconds, so the bot stays
ONLINE in the dashboard for as long as this script runs. Stop it (Ctrl+C) and
the watchdog will mark it offline after the timeout, opening an incident.

Run the server first, then:
    python live_agent.py                       # default: live-agent-01 / prod
    python live_agent.py my-bot staging        # custom id / environment

Override target with WATCHDOG_URL (default ws://localhost:8000).
"""
import asyncio
import json
import os
import random
import sys

import websockets

BASE_WS = os.getenv("WATCHDOG_URL", "ws://localhost:8000").rstrip("/")
BASE_WS = BASE_WS.replace("http://", "ws://").replace("https://", "wss://")

BOT_ID = sys.argv[1] if len(sys.argv) > 1 else "live-agent-01"
ENV = sys.argv[2] if len(sys.argv) > 2 else "prod"
INTERVAL = 3  # seconds between heartbeats


def _metrics() -> dict:
    """Realistic, fluctuating health metrics. ~1 in 12 ticks simulates a spike."""
    if random.random() < 0.08:  # occasional degraded reading
        return {
            "inference_latency_p95_ms": round(random.uniform(2800, 4200), 0),
            "llm_error_rate": round(random.uniform(0.08, 0.25), 3),
            "queue_depth": random.randint(180, 320),
        }
    return {
        "inference_latency_p95_ms": round(random.uniform(380, 900), 0),
        "llm_error_rate": round(random.uniform(0.0, 0.03), 3),
        "queue_depth": random.randint(0, 12),
    }


async def main():
    url = f"{BASE_WS}/ws/agent?bot_id={BOT_ID}&environment={ENV}"
    print(f"Connecting {BOT_ID} ({ENV}) -> {url}")
    seq = 0
    async with websockets.connect(url) as ws:
        print("Connected. Sending heartbeats every %ds (Ctrl+C to stop)" % INTERVAL)
        while True:
            seq += 1
            # A 'health' message doubles as a heartbeat and carries AI metrics.
            m = _metrics()
            await ws.send(json.dumps({"type": "health", "seq": seq, "metrics": m}))
            ack = json.loads(await ws.recv())
            print(f"  hb #{seq} -> {ack.get('type')} | lat={m['inference_latency_p95_ms']:.0f}ms "
                  f"err={m['llm_error_rate']*100:.1f}% cola={m['queue_depth']}")
            await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped. The watchdog will mark this bot offline after the timeout.")
