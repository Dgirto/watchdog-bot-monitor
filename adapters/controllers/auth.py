"""
adapters/controllers/auth.py
HMAC verification for heartbeats — stops heartbeat spoofing (S-2).

The agent signs `bot_id|environment|timestamp` with its secret and sends:
    HTTP : headers  X-Timestamp / X-Signature
    WS   : query params  ts / sig  (browsers can't set WS headers)

Three modes (HEARTBEAT_AUTH_MODE):
    off     — verification disabled
    warn    — accept unsigned/unknown but log a warning (migration default)
    enforce — reject anything not correctly signed
"""
import hashlib
import hmac
import logging
import time
from typing import Optional

from fastapi import HTTPException, status

from infrastructure.config import settings
from infrastructure.secrets import get_agent_secret

logger = logging.getLogger("watchdog.auth")

# Max clock skew (seconds) tolerated — also the anti-replay window.
MAX_SKEW_SECONDS = 30


class AuthError(Exception):
    """Raised by the transport-agnostic core when verification fails."""


def _expected_signature(secret: str, bot_id: str, environment: str, timestamp: str) -> str:
    msg = f"{bot_id}|{environment}|{timestamp}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_signature(
    bot_id: str,
    environment: str,
    timestamp: Optional[str],
    signature: Optional[str],
) -> None:
    """Transport-agnostic check. Raises AuthError on failure, no-op when valid."""
    mode = settings.HEARTBEAT_AUTH_MODE

    if mode == "off":
        return

    if timestamp is None or signature is None:
        if mode == "enforce":
            raise AuthError("missing signature")
        logger.warning("Unsigned heartbeat accepted (warn mode) | bot_id=%s", bot_id)
        return

    secret = get_agent_secret(bot_id)
    if not secret:
        if mode == "enforce":
            raise AuthError("unknown agent")
        logger.warning("No secret configured for bot_id=%s; accepting (warn mode)", bot_id)
        return

    try:
        ts = int(timestamp)
    except ValueError:
        raise AuthError("invalid timestamp")

    if abs(time.time() - ts) > MAX_SKEW_SECONDS:
        raise AuthError("stale timestamp (possible replay)")

    expected = _expected_signature(secret, bot_id, environment, timestamp)
    if not hmac.compare_digest(expected, signature):  # constant-time, anti-timing
        raise AuthError("bad signature")


def verify_heartbeat_auth(
    bot_id: str,
    environment: str,
    timestamp: Optional[str],
    signature: Optional[str],
) -> None:
    """HTTP wrapper: turns AuthError into HTTPException(401)."""
    try:
        verify_signature(bot_id, environment, timestamp, signature)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
