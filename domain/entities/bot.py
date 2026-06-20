"""
domain/entities/bot.py
Core business entities — zero framework dependencies.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


def _utcnow() -> datetime:
    """Timezone-aware UTC now (domain stays stdlib-only, no infra import)."""
    return datetime.now(timezone.utc)


class BotStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class BotEnvironment(str, Enum):
    PROD = "prod"
    STAGING = "staging"
    DEV = "dev"


@dataclass
class Bot:
    """Represents a registered bot and its current availability state."""
    bot_id: str
    name: str
    environment: BotEnvironment
    status: BotStatus = BotStatus.UNKNOWN
    last_seen: Optional[datetime] = None
    registered_at: datetime = field(default_factory=_utcnow)

    def record_heartbeat(self, timestamp: datetime) -> None:
        self.last_seen = timestamp
        self.status = BotStatus.ONLINE

    def mark_offline(self) -> None:
        self.status = BotStatus.OFFLINE

    def is_stale(self, timeout_seconds: int, grace_seconds: int, now: datetime) -> bool:
        """True if the bot has not reported within timeout + grace window."""
        if self.last_seen is None:
            return True
        elapsed = (now - self.last_seen).total_seconds()
        return elapsed > (timeout_seconds + grace_seconds)


@dataclass
class Incident:
    """Records an availability incident: when a bot went offline and recovered."""
    incident_id: str
    bot_id: str
    environment: BotEnvironment
    offline_at: datetime
    recovered_at: Optional[datetime] = None
    downtime_seconds: Optional[float] = None

    def resolve(self, recovered_at: datetime) -> None:
        self.recovered_at = recovered_at
        # Clamp at 0: clock skew / out-of-order timestamps must never yield a
        # negative downtime.
        self.downtime_seconds = max(0.0, (recovered_at - self.offline_at).total_seconds())

    @property
    def is_active(self) -> bool:
        return self.recovered_at is None
