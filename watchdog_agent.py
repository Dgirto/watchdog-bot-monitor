"""
watchdog_agent.py
Agente mínimo: tu bot le dice al Watchdog "estoy vivo" por WebSocket.

USO:
  1) pip install websockets
  2) Edita los 4 valores de CONFIG aquí abajo.
  3) python watchdog_agent.py

Se reconecta solo si se cae la red o el servidor. Para parar: Ctrl+C.
"""
import asyncio
import hashlib
import hmac
import json
import random
import time

import websockets

# ─────────────────────────── CONFIG ───────────────────────────
WATCHDOG_URL = "ws://localhost:8000"   # host del watchdog (ws:// o wss://)
BOT_ID       = "mi-bot-01"             # identificador único de tu bot
ENVIRONMENT  = "prod"                  # prod | staging | dev
SECRET       = ""                      # déjalo "" salvo que el server esté en modo enforce
INTERVAL     = 15                      # segundos entre latidos
# ───────────────────────────────────────────────────────────────


def _auth_suffix() -> str:
    """Firma HMAC en la URL. Vacío si no hay SECRET (modo warn/off)."""
    if not SECRET:
        return ""
    ts = str(int(time.time()))
    sig = hmac.new(SECRET.encode(), f"{BOT_ID}|{ENVIRONMENT}|{ts}".encode(), hashlib.sha256).hexdigest()
    return f"&ts={ts}&sig={sig}"


async def run() -> None:
    base = WATCHDOG_URL.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    backoff = 1
    while True:
        url = f"{base}/ws/agent?bot_id={BOT_ID}&environment={ENVIRONMENT}{_auth_suffix()}"
        try:
            async with websockets.connect(url) as ws:
                print(f"[watchdog] conectado como {BOT_ID} ({ENVIRONMENT})", flush=True)
                backoff = 1
                seq = 0
                while True:
                    seq += 1
                    await ws.send(json.dumps({"type": "heartbeat", "seq": seq}))
                    await ws.recv()  # ack del servidor
                    await asyncio.sleep(INTERVAL)
        except Exception as exc:  # red caída, server reiniciado, etc.
            wait = min(backoff, 30) + random.uniform(0, 1)
            print(f"[watchdog] desconectado ({exc}); reintentando en {wait:.0f}s", flush=True)
            await asyncio.sleep(wait)
            backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[watchdog] detenido")
