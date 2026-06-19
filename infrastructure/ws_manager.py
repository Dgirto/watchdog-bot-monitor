"""
infrastructure/ws_manager.py
Tracks live agent WebSocket connections, grouped into rooms by (bot_id, env).

Rooms let us push targeted control commands to a specific agent (and, later,
broadcast across instances via Redis pub/sub in the HA phase). State is
per-process; multi-instance fan-out is a Fase 3b concern.
"""
import logging
from typing import Dict, Set, Tuple

from fastapi import WebSocket

logger = logging.getLogger("watchdog.ws")

RoomKey = Tuple[str, str]  # (bot_id, environment)


class WSManager:
    def __init__(self):
        self._rooms: Dict[RoomKey, Set[WebSocket]] = {}

    async def connect(self, key: RoomKey, ws: WebSocket) -> None:
        self._rooms.setdefault(key, set()).add(ws)
        logger.info("WS connected | bot_id=%s env=%s (conns=%d)", key[0], key[1], len(self._rooms[key]))

    def disconnect(self, key: RoomKey, ws: WebSocket) -> None:
        room = self._rooms.get(key)
        if not room:
            return
        room.discard(ws)
        if not room:
            self._rooms.pop(key, None)
        logger.info("WS disconnected | bot_id=%s env=%s", key[0], key[1])

    def is_connected(self, key: RoomKey) -> bool:
        return bool(self._rooms.get(key))

    async def send_command(self, key: RoomKey, action: str) -> int:
        """Push a control command to every live socket of an agent. Returns count."""
        room = self._rooms.get(key, set())
        sent = 0
        for ws in list(room):
            try:
                await ws.send_json({"type": "command", "action": action})
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("WS command send failed: %s", exc)
        return sent


ws_manager = WSManager()
