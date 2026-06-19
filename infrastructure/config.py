"""
infrastructure/config.py  — all tunable settings from env vars
infrastructure/container.py — dependency wiring (DI container)
"""
# ── config.py ────────────────────────────────────────────────────
import os


class Settings:
    # How long without a heartbeat before a bot is considered stale
    HEARTBEAT_TIMEOUT_SECONDS: int = int(os.getenv("HEARTBEAT_TIMEOUT", "60"))

    # Extra grace window to absorb network jitter / latency spikes
    GRACE_PERIOD_SECONDS: int = int(os.getenv("GRACE_PERIOD", "15"))

    # How often the watchdog sweep runs
    WATCHDOG_INTERVAL_SECONDS: int = int(os.getenv("WATCHDOG_INTERVAL", "30"))

    # Optional: Slack/Discord/Teams webhook URL (secondary alert channel)
    WEBHOOK_URL: str | None = os.getenv("ALERT_WEBHOOK_URL")

    # ── Email (primary alert channel, via SendGrid) ──────────────────
    EMAIL_RECIPIENTS: list[str] = [
        r.strip() for r in os.getenv("ALERT_EMAIL_RECIPIENTS", "").split(",") if r.strip()
    ]
    SENDGRID_API_KEY: str | None = os.getenv("SENDGRID_API_KEY")
    ALERT_EMAIL_SENDER: str | None = os.getenv("ALERT_EMAIL_SENDER")

    # ── Smart alert thresholds (anti-glitch / anti-flapping) ─────────
    # Seconds a bot must stay offline before an alert actually fires.
    ALERT_CONFIRM_SECONDS: int = int(os.getenv("ALERT_CONFIRM_SECONDS", "90"))
    # Silence window after an alert, to prevent flap storms.
    ALERT_COOLDOWN_SECONDS: int = int(os.getenv("ALERT_COOLDOWN_SECONDS", "300"))

    # ── Heartbeat authentication (HMAC) ──────────────────────────────
    # off | warn | enforce   (warn = accept unsigned but log, for migration)
    HEARTBEAT_AUTH_MODE: str = os.getenv("HEARTBEAT_AUTH_MODE", "warn")
    AGENT_SHARED_SECRET: str | None = os.getenv("AGENT_SHARED_SECRET")

    DB_PATH: str = os.getenv("DB_PATH", "watchdog.db")


settings = Settings()
