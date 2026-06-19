"""
adapters/repositories/postgres_repositories.py
PostgreSQL adapters — same ports as the SQLite ones, swappable via DB_BACKEND.

Why Postgres (Fase 2):
  - real concurrent writes (SQLite serializes on a single writer)
  - a connection pool instead of connect-per-operation
  - TIMESTAMPTZ columns store tz-aware UTC natively
  - enables HA (multiple monitor instances) in Fase 3

Set DATABASE_URL, e.g.:
    postgresql://user:pass@localhost:5432/watchdog
"""
from datetime import datetime
from typing import List, Optional, Set, Tuple
import uuid

import asyncpg

from domain.entities.bot import Bot, BotEnvironment, BotStatus, Incident
from domain.entities.health import HealthMetrics
from domain.interfaces.repositories import IBotRepository, IIncidentRepository, IHealthRepository
from infrastructure.config import settings
from infrastructure.time import ensure_utc


# ──────────────────────────────────────────────────────────────────
# Shared connection pool (lazy)
# ──────────────────────────────────────────────────────────────────
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, min_size=2, max_size=10)
    return _pool


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bots (
    bot_id          TEXT        NOT NULL,
    name            TEXT        NOT NULL,
    environment     TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'unknown',
    last_seen       TIMESTAMPTZ,
    registered_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (bot_id, environment)
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id      TEXT PRIMARY KEY,
    bot_id           TEXT NOT NULL,
    environment      TEXT NOT NULL,
    offline_at       TIMESTAMPTZ NOT NULL,
    recovered_at     TIMESTAMPTZ,
    downtime_seconds DOUBLE PRECISION,
    FOREIGN KEY (bot_id, environment) REFERENCES bots (bot_id, environment)
);

CREATE INDEX IF NOT EXISTS idx_incidents_bot ON incidents (bot_id, environment);
CREATE INDEX IF NOT EXISTS idx_incidents_active ON incidents (bot_id, environment)
    WHERE recovered_at IS NULL;

CREATE TABLE IF NOT EXISTS health_metrics (
    bot_id                   TEXT        NOT NULL,
    environment              TEXT        NOT NULL,
    recorded_at              TIMESTAMPTZ NOT NULL,
    inference_latency_p95_ms DOUBLE PRECISION,
    llm_error_rate           DOUBLE PRECISION,
    queue_depth              INTEGER
);

CREATE INDEX IF NOT EXISTS idx_health_bot ON health_metrics (bot_id, environment, recorded_at DESC);
"""


async def init_db() -> None:
    pool = await get_pool()
    async with pool.acquire() as con:
        await con.execute(SCHEMA_SQL)


# ──────────────────────────────────────────────────────────────────
# Bot Repository
# ──────────────────────────────────────────────────────────────────
class PostgresBotRepository(IBotRepository):

    def _row_to_bot(self, row) -> Bot:
        return Bot(
            bot_id=row["bot_id"],
            name=row["name"],
            environment=BotEnvironment(row["environment"]),
            status=BotStatus(row["status"]),
            last_seen=ensure_utc(row["last_seen"]),
            registered_at=ensure_utc(row["registered_at"]),
        )

    async def upsert(self, bot: Bot) -> Bot:
        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO bots (bot_id, name, environment, status, last_seen, registered_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (bot_id, environment) DO UPDATE SET
                name      = EXCLUDED.name,
                status    = EXCLUDED.status,
                last_seen = EXCLUDED.last_seen
            """,
            bot.bot_id, bot.name, bot.environment.value, bot.status.value,
            bot.last_seen, bot.registered_at,
        )
        return bot

    async def find_by_id(self, bot_id: str, environment: str) -> Optional[Bot]:
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM bots WHERE bot_id = $1 AND environment = $2",
            bot_id, environment,
        )
        return self._row_to_bot(row) if row else None

    async def find_all(self) -> List[Bot]:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT * FROM bots ORDER BY environment, bot_id"
        )
        return [self._row_to_bot(r) for r in rows]

    async def update_status(self, bot_id: str, environment: str, status: str) -> None:
        pool = await get_pool()
        await pool.execute(
            "UPDATE bots SET status = $1 WHERE bot_id = $2 AND environment = $3",
            status, bot_id, environment,
        )


# ──────────────────────────────────────────────────────────────────
# Incident Repository
# ──────────────────────────────────────────────────────────────────
class PostgresIncidentRepository(IIncidentRepository):

    def _row_to_incident(self, row) -> Incident:
        return Incident(
            incident_id=row["incident_id"],
            bot_id=row["bot_id"],
            environment=BotEnvironment(row["environment"]),
            offline_at=ensure_utc(row["offline_at"]),
            recovered_at=ensure_utc(row["recovered_at"]),
            downtime_seconds=row["downtime_seconds"],
        )

    async def open_incident(self, incident: Incident) -> Incident:
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO incidents (incident_id, bot_id, environment, offline_at) "
            "VALUES ($1, $2, $3, $4)",
            incident.incident_id, incident.bot_id, incident.environment.value, incident.offline_at,
        )
        return incident

    async def close_incident(self, bot_id: str, environment: str, recovered_at: datetime) -> Optional[Incident]:
        pool = await get_pool()
        async with pool.acquire() as con:
            async with con.transaction():
                row = await con.fetchrow(
                    "SELECT * FROM incidents "
                    "WHERE bot_id = $1 AND environment = $2 AND recovered_at IS NULL "
                    "ORDER BY offline_at DESC LIMIT 1 FOR UPDATE",
                    bot_id, environment,
                )
                if not row:
                    return None
                incident = self._row_to_incident(row)
                incident.resolve(recovered_at)
                await con.execute(
                    "UPDATE incidents SET recovered_at = $1, downtime_seconds = $2 "
                    "WHERE incident_id = $3",
                    incident.recovered_at, incident.downtime_seconds, incident.incident_id,
                )
        return incident

    async def find_active_incident(self, bot_id: str, environment: str) -> Optional[Incident]:
        pool = await get_pool()
        row = await pool.fetchrow(
            "SELECT * FROM incidents "
            "WHERE bot_id = $1 AND environment = $2 AND recovered_at IS NULL",
            bot_id, environment,
        )
        return self._row_to_incident(row) if row else None

    async def find_active_bot_keys(self) -> Set[Tuple[str, str]]:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT bot_id, environment FROM incidents WHERE recovered_at IS NULL"
        )
        return {(r["bot_id"], r["environment"]) for r in rows}

    async def find_all(self, limit: int = 100) -> List[Incident]:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT * FROM incidents ORDER BY offline_at DESC LIMIT $1",
            limit,
        )
        return [self._row_to_incident(r) for r in rows]


# ──────────────────────────────────────────────────────────────────
# Health Metrics Repository
# ──────────────────────────────────────────────────────────────────
class PostgresHealthRepository(IHealthRepository):

    def _row_to_metrics(self, row) -> HealthMetrics:
        return HealthMetrics(
            bot_id=row["bot_id"],
            environment=BotEnvironment(row["environment"]),
            recorded_at=ensure_utc(row["recorded_at"]),
            inference_latency_p95_ms=row["inference_latency_p95_ms"],
            llm_error_rate=row["llm_error_rate"],
            queue_depth=row["queue_depth"],
        )

    async def save(self, metrics: HealthMetrics) -> None:
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO health_metrics (bot_id, environment, recorded_at, "
            "inference_latency_p95_ms, llm_error_rate, queue_depth) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            metrics.bot_id, metrics.environment.value, metrics.recorded_at,
            metrics.inference_latency_p95_ms, metrics.llm_error_rate, metrics.queue_depth,
        )

    async def find_recent(self, bot_id: str, environment: str, limit: int = 50) -> List[HealthMetrics]:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT * FROM health_metrics WHERE bot_id = $1 AND environment = $2 "
            "ORDER BY recorded_at DESC LIMIT $3",
            bot_id, environment, limit,
        )
        return [self._row_to_metrics(r) for r in rows]

    async def find_latest_all(self) -> List[HealthMetrics]:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT DISTINCT ON (bot_id, environment) * FROM health_metrics "
            "ORDER BY bot_id, environment, recorded_at DESC"
        )
        return [self._row_to_metrics(r) for r in rows]
