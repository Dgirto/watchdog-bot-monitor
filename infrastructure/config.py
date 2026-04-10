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

    # Optional: Slack/Discord/Teams webhook URL
    WEBHOOK_URL: str | None = os.getenv("ALERT_WEBHOOK_URL")

    # Optional: comma-separated list of email recipients
    EMAIL_RECIPIENTS: list[str] = [
        r for r in os.getenv("ALERT_EMAIL_RECIPIENTS", "").split(",") if r
    ]

    DB_PATH: str = os.getenv("DB_PATH", "watchdog.db")


settings = Settings()
