# 🐕 Watchdog — Bot Availability Monitor

Lightweight uptime monitor for production bots. Zero hardware metrics.
Pure availability tracking via heartbeat signals.

---

## Database Schema

```sql
-- Registered bots and their current state
CREATE TABLE bots (
    bot_id          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    environment     TEXT    NOT NULL,          -- prod | staging | dev
    status          TEXT    NOT NULL DEFAULT 'unknown',   -- online | offline | unknown
    last_seen       TEXT,                      -- ISO-8601 UTC timestamp
    registered_at   TEXT    NOT NULL,
    PRIMARY KEY (bot_id, environment)          -- same bot can run in multiple envs
);

-- One row per outage. Closed automatically on next heartbeat.
CREATE TABLE incidents (
    incident_id      TEXT PRIMARY KEY,
    bot_id           TEXT NOT NULL,
    environment      TEXT NOT NULL,
    offline_at       TEXT NOT NULL,            -- when watchdog detected the outage
    recovered_at     TEXT,                     -- NULL while incident is active
    downtime_seconds REAL,                     -- auto-calculated on recovery
    FOREIGN KEY (bot_id, environment) REFERENCES bots(bot_id, environment)
);
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) configure via env vars
export HEARTBEAT_TIMEOUT=60    # seconds without heartbeat → offline
export GRACE_PERIOD=15         # extra buffer for network jitter
export WATCHDOG_INTERVAL=30    # sweep frequency
export ALERT_WEBHOOK_URL=https://hooks.slack.com/...   # optional

# 3. Run
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## API

| Method | Path         | Description                         |
|--------|--------------|-------------------------------------|
| POST   | /heartbeat   | Bot reports it is alive             |
| GET    | /status      | Full fleet status as JSON           |
| GET    | /dashboard   | Web UI with visual indicators       |
| GET    | /health      | Service liveness check              |
| GET    | /docs        | Interactive API docs (Swagger UI)   |

## Project Structure

```
watchdog/
├── domain/
│   ├── entities/bot.py          # Bot, Incident — pure Python, no deps
│   └── interfaces/repositories.py  # Abstract ports
├── use_cases/
│   └── watchdog.py              # ProcessHeartbeat, RunWatchdog
├── adapters/
│   ├── repositories/
│   │   └── sqlite_repositories.py   # SQLite implementation (swap for Postgres)
│   └── controllers/
│       ├── api.py               # POST /heartbeat, GET /status
│       └── dashboard.py         # GET /dashboard
├── notifications/
│   └── manager.py               # NotificationManager + channels
├── infrastructure/
│   ├── config.py                # Settings from env vars
│   └── container.py             # Dependency injection wiring
├── bot_client_example.py        # Drop-in heartbeat client for any bot
├── main.py                      # FastAPI app + watchdog background task
└── requirements.txt
```

## Adding a Notification Channel

```python
# notifications/manager.py
class MyCustomChannel(NotificationChannel):
    async def send(self, event: StatusChangeEvent) -> None:
        # your logic: PagerDuty, Telegram, SMS, etc.
        ...

# infrastructure/container.py — add to channels list
channels.append(MyCustomChannel(...))
```

## Swapping the Database

Implement `IBotRepository` and `IIncidentRepository` for PostgreSQL/MySQL,
then replace `SqliteBotRepository` and `SqliteIncidentRepository` in
`infrastructure/container.py`. The rest of the codebase is untouched.
