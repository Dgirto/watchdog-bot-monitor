"""
infrastructure/secrets.py
Resolves the shared secret used to verify heartbeat signatures (HMAC).

Per-agent secrets take precedence; a single shared secret is the fallback so
small fleets can roll out auth without provisioning one secret per bot.

Configure via env vars:
    AGENT_SECRETS="price-tracker-01:s3cr3t,scraper-02:an0ther"
    AGENT_SHARED_SECRET="fleet-wide-secret"
"""
import os
from typing import Optional

_per_agent: dict[str, str] = {}
for _pair in os.getenv("AGENT_SECRETS", "").split(","):
    if ":" in _pair:
        _k, _v = _pair.split(":", 1)
        if _k.strip():
            _per_agent[_k.strip()] = _v.strip()

_shared: Optional[str] = os.getenv("AGENT_SHARED_SECRET") or None


def get_agent_secret(bot_id: str) -> Optional[str]:
    """Return the secret for a bot, or the shared fallback, or None."""
    return _per_agent.get(bot_id) or _shared
