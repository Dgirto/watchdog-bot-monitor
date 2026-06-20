"""
adapters/controllers/ws.py
WebSocket endpoint for real-time agent monitoring.

Additive: agents that can't speak WS keep using POST /heartbeat (the HTTP
fallback). WS and HTTP both drive the same ProcessHeartbeatUseCase, so there is
a single source of truth for liveness.

Handshake auth uses the same HMAC as HTTP, via query params (browsers can't set
custom WS headers): /ws/agent?bot_id=..&environment=..&ts=..&sig=..

Protocol (JSON text frames):
    agent → server : {"type":"heartbeat","seq":N}
                     {"type":"health","metrics":{...}}
    server → agent : {"type":"ack","seq":N}
                     {"type":"error","detail":"..."}
                     {"type":"command","action":"drain"|"shutdown"}
"""
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from adapters.controllers.api import VALID_ENVS, _ID_RE
from adapters.controllers.auth import AuthError, verify_signature
from infrastructure.container import container
from infrastructure.ws_manager import ws_manager
from use_cases.watchdog import HeartbeatRequest

logger = logging.getLogger("watchdog.ws")
ws_router = APIRouter()

# Close codes (4000-4999 = application-defined)
WS_POLICY_VIOLATION = 1008


@ws_router.websocket("/ws/agent")
async def agent_ws(ws: WebSocket) -> None:
    bot_id = ws.query_params.get("bot_id", "")
    environment = ws.query_params.get("environment", "")

    # Validate identity shape before doing anything else.
    if not _ID_RE.match(bot_id) or environment not in VALID_ENVS:
        await ws.close(code=WS_POLICY_VIOLATION, reason="invalid bot_id/environment")
        return

    # Same HMAC verification as HTTP heartbeats.
    try:
        verify_signature(bot_id, environment, ws.query_params.get("ts"), ws.query_params.get("sig"))
    except AuthError as exc:
        await ws.close(code=WS_POLICY_VIOLATION, reason=str(exc))
        return

    await ws.accept()
    key = (bot_id, environment)
    await ws_manager.connect(key, ws)

    try:
        while True:
            raw = await ws.receive_text()

            # A single malformed frame must not drop a healthy connection.
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError("payload must be a JSON object")
            except (json.JSONDecodeError, ValueError):
                await ws.send_json({"type": "error", "detail": "invalid JSON payload"})
                continue

            mtype = msg.get("type")
            if mtype in ("heartbeat", "health"):
                # Any message proves liveness → update last_seen / close incidents.
                await container.process_heartbeat.execute(
                    HeartbeatRequest(bot_id=bot_id, environment=environment)
                )
                if mtype == "health":
                    await container.record_health.execute(
                        bot_id, environment, msg.get("metrics", {}) or {}
                    )
                await ws.send_json({"type": "ack", "seq": msg.get("seq")})
            else:
                await ws.send_json({"type": "error", "detail": f"unknown message type: {mtype}"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.error("WS error | bot_id=%s: %s", bot_id, exc)
    finally:
        ws_manager.disconnect(key, ws)
