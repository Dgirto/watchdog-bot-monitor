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

# ── Heartbeat authentication (HMAC) ──────────────────────────────
export HEARTBEAT_AUTH_MODE=warn          # off | warn | enforce
export AGENT_SHARED_SECRET=change-me      # or per-agent: AGENT_SECRETS="bot1:s1,bot2:s2"

# ── Alerts: email primary, Slack secondary ───────────────────────
export SENDGRID_API_KEY=SG.xxxxx
export ALERT_EMAIL_SENDER=watchdog@yourco.com
export ALERT_EMAIL_RECIPIENTS=oncall@yourco.com,sre@yourco.com
export ALERT_WEBHOOK_URL=https://hooks.slack.com/...   # optional

# ── Smart alert thresholds (anti-glitch / anti-flapping) ─────────
export ALERT_CONFIRM_SECONDS=90     # must stay offline this long before alerting
export ALERT_COOLDOWN_SECONDS=300   # silence window after an alert

# 3. Run
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Heartbeat Authentication (zero-downtime rollout)

Heartbeats are signed with HMAC-SHA256 to stop spoofing. The client sends
`X-Timestamp` and `X-Signature` headers (see `bot_client_example.py`, pass
`secret=...`). Roll it out without breaking existing agents:

1. **`warn`** (default) — server accepts signed *and* unsigned heartbeats, logging
   a warning for unsigned ones. Update agents to sign at your own pace.
2. **`enforce`** — once every agent signs, flip `HEARTBEAT_AUTH_MODE=enforce`.
   Unsigned / bad-signature / stale (replay) heartbeats are rejected with `401`.

## API

| Method | Path                       | Description                              |
|--------|----------------------------|------------------------------------------|
| WS     | /ws/agent                  | Real-time agent connection (heartbeat + health) |
| POST   | /heartbeat                 | HTTP fallback — bot reports it is alive  |
| GET    | /status                    | Full fleet status as JSON                |
| GET    | /agents/{bot_id}/health    | Recent AI health metrics for an agent    |
| GET    | /dashboard                 | Web UI with visual indicators            |
| GET    | /health                    | Service liveness check                   |
| GET    | /docs                      | Interactive API docs (Swagger UI)        |

## Real-time monitoring over WebSocket

Agents connect to `/ws/agent` for low-latency monitoring; agents that can't speak
WS keep using `POST /heartbeat` (both drive the same liveness logic, so there is
one source of truth). The client auto-reconnects with exponential backoff + jitter.

Handshake auth uses the same HMAC as HTTP, via query params (browsers can't set
WS headers): `/ws/agent?bot_id=..&environment=..&ts=..&sig=..`

```jsonc
// agent → server
{ "type": "heartbeat", "seq": 1 }
{ "type": "health", "seq": 2, "metrics": { "tokens_per_sec": 47.3, "llm_error_rate": 0.01 } }
// server → agent
{ "type": "ack", "seq": 2 }
```

### AI-agent health metrics

What separates an *AI agent* from a generic bot: it can be **online yet degraded
or burning money**. Reported via the `health` message and stored per report:

| Metric | Meaning |
|--------|---------|
| `inference_latency_p95_ms` | LLM responsiveness |
| `tokens_per_sec`           | Useful throughput |
| `llm_error_rate`           | 0..1 — alive but failing |
| `session_cost_usd`         | Runaway-cost guard |
| `queue_depth`              | Backpressure / saturation |

Anomalies (e.g. error rate > 5%, cost spikes) are flagged in the logs.

## Project Structure

```
watchdog/
├── domain/
│   ├── entities/bot.py          # Bot, Incident — pure Python, no deps
│   ├── entities/health.py       # HealthMetrics — AI-agent metrics
│   └── interfaces/repositories.py  # Abstract ports (bot, incident, health)
├── use_cases/
│   ├── watchdog.py              # ProcessHeartbeat, RunWatchdog
│   └── health.py               # RecordHealth + anomaly detection
├── adapters/
│   ├── repositories/
│   │   ├── sqlite_repositories.py    # SQLite backend
│   │   └── postgres_repositories.py  # PostgreSQL backend (asyncpg)
│   └── controllers/
│       ├── api.py               # POST /heartbeat, GET /status, GET /agents/{id}/health
│       ├── auth.py              # HMAC verification (HTTP + WS)
│       ├── ws.py                # WS /ws/agent — real-time transport
│       └── dashboard.py         # GET /dashboard
├── notifications/
│   ├── manager.py               # NotificationManager + channels (Log/Email/Webhook)
│   └── throttler.py             # Smart alert thresholds (debounce + cooldown)
├── infrastructure/
│   ├── config.py                # Settings from env vars
│   ├── secrets.py               # Per-agent / shared HMAC secrets
│   ├── time.py                  # tz-aware UTC helpers
│   ├── ws_manager.py            # Live WS connections, rooms per agent
│   └── container.py             # Dependency injection wiring
├── bot_client_example.py        # HTTP + WebSocket heartbeat clients
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

## Database Backend (SQLite or PostgreSQL)

Selected at startup with `DB_BACKEND`; both implement the same ports, so no
business logic changes.

```bash
# Default — zero config
export DB_BACKEND=sqlite
export DB_PATH=watchdog.db

# Production — concurrent writes, connection pool, HA-ready
export DB_BACKEND=postgres
export DATABASE_URL=postgresql://user:pass@localhost:5432/watchdog
pip install asyncpg   # only needed for the postgres backend
```

`PostgresBotRepository`/`PostgresIncidentRepository` use an `asyncpg` pool and
`TIMESTAMPTZ` columns. The schema is created automatically on startup.

To add another backend (MySQL, etc.), implement `IBotRepository` and
`IIncidentRepository` and wire it in `infrastructure/container.py`.
