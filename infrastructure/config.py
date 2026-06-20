"""
infrastructure/config.py — typed, validated application settings.

All tunables come from environment variables. Settings is an *instance* (not a
bag of class attributes) so it can be reconstructed in tests and validated
at startup — misconfiguration fails fast with a clear message instead of
surfacing as a cryptic runtime error later.
"""
import logging
import os
from typing import List, Optional

logger = logging.getLogger("watchdog.config")

VALID_AUTH_MODES = ("off", "warn", "enforce")
VALID_BACKENDS = ("sqlite", "postgres")
_TRUTHY = ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; falling back to %d", name, raw, default)
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


class Settings:
    """Snapshot of the environment. Construct once; inject everywhere."""

    def __init__(self) -> None:
        # Watchdog timing
        self.HEARTBEAT_TIMEOUT_SECONDS = _get_int("HEARTBEAT_TIMEOUT", 60)
        self.GRACE_PERIOD_SECONDS = _get_int("GRACE_PERIOD", 15)
        self.WATCHDOG_INTERVAL_SECONDS = _get_int("WATCHDOG_INTERVAL", 30)

        # Alert channels
        self.WEBHOOK_URL: Optional[str] = os.getenv("ALERT_WEBHOOK_URL") or None
        self.EMAIL_RECIPIENTS: List[str] = [
            r.strip() for r in os.getenv("ALERT_EMAIL_RECIPIENTS", "").split(",") if r.strip()
        ]
        self.SENDGRID_API_KEY: Optional[str] = os.getenv("SENDGRID_API_KEY") or None
        self.ALERT_EMAIL_SENDER: Optional[str] = os.getenv("ALERT_EMAIL_SENDER") or None

        # Smart alert thresholds
        self.ALERT_CONFIRM_SECONDS = _get_int("ALERT_CONFIRM_SECONDS", 90)
        self.ALERT_COOLDOWN_SECONDS = _get_int("ALERT_COOLDOWN_SECONDS", 300)

        # Heartbeat auth (HMAC)
        self.HEARTBEAT_AUTH_MODE = os.getenv("HEARTBEAT_AUTH_MODE", "warn")
        self.AGENT_SHARED_SECRET: Optional[str] = os.getenv("AGENT_SHARED_SECRET") or None
        self.AGENT_SECRETS = os.getenv("AGENT_SECRETS", "")

        # Database backend
        self.DB_BACKEND = os.getenv("DB_BACKEND", "sqlite")
        self.DB_PATH = os.getenv("DB_PATH", "watchdog.db")
        self.DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL") or None

        # Runtime environment
        self.APP_ENV = os.getenv("APP_ENV", "dev")
        self._expose_docs_override = os.getenv("EXPOSE_DOCS")

    @property
    def DOCS_ENABLED(self) -> bool:
        """Interactive API docs (/docs, /openapi.json). Off in prod unless
        explicitly enabled — they leak the full API surface (S-6)."""
        if self._expose_docs_override is not None:
            return self._expose_docs_override.strip().lower() in _TRUTHY
        return self.APP_ENV != "prod"

    def validate(self) -> List[str]:
        """Fatal misconfigurations. Startup must abort if this is non-empty."""
        errors: List[str] = []
        if self.DB_BACKEND not in VALID_BACKENDS:
            errors.append(f"DB_BACKEND must be one of {VALID_BACKENDS}, got {self.DB_BACKEND!r}")
        if self.DB_BACKEND == "postgres" and not self.DATABASE_URL:
            errors.append("DB_BACKEND=postgres requires DATABASE_URL")
        if self.HEARTBEAT_AUTH_MODE not in VALID_AUTH_MODES:
            errors.append(f"HEARTBEAT_AUTH_MODE must be one of {VALID_AUTH_MODES}, got {self.HEARTBEAT_AUTH_MODE!r}")
        if self.HEARTBEAT_AUTH_MODE == "enforce" and not (self.AGENT_SHARED_SECRET or self.AGENT_SECRETS.strip()):
            errors.append("HEARTBEAT_AUTH_MODE=enforce requires AGENT_SHARED_SECRET or AGENT_SECRETS")
        if self.SENDGRID_API_KEY and not self.ALERT_EMAIL_SENDER:
            errors.append("SENDGRID_API_KEY is set but ALERT_EMAIL_SENDER is missing")
        return errors

    def warnings(self) -> List[str]:
        """Non-fatal but risky configurations worth surfacing at startup."""
        warns: List[str] = []
        if self.APP_ENV == "prod" and self.HEARTBEAT_AUTH_MODE != "enforce":
            warns.append(
                f"prod with HEARTBEAT_AUTH_MODE={self.HEARTBEAT_AUTH_MODE!r} — "
                "heartbeats are not fully authenticated"
            )
        if self.APP_ENV == "prod" and self.DB_BACKEND == "sqlite":
            warns.append("prod on sqlite — consider DB_BACKEND=postgres for concurrency and HA")
        return warns


settings = Settings()
