"""
adapters/repositories/sqlite_repositories.py
Concrete SQLite adapters. Swap for PostgreSQL/MySQL by implementing the same ports.
"""
import aiosqlite
from datetime import datetime
from typing import List, Optional
import uuid

from domain.entities.bot import Bot, BotEnvironment, BotStatus, Incident
from domain.interfaces.repositories import IBotRepository, IIncidentRepository


DB_PATH = "watchdog.db"

# ──────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────
SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

-- Registered bots and their current state
CREATE TABLE IF NOT EXISTS bots (
    bot_id          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    environment     TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'unknown',
    last_seen       TEXT,                          -- ISO-8601 UTC
    registered_at   TEXT    NOT NULL,
    PRIMARY KEY (bot_id, environment)
);

-- Availability incident log (one row per outage)
CREATE TABLE IF NOT EXISTS incidents (
    incident_id      TEXT PRIMARY KEY,
    bot_id           TEXT NOT NULL,
    environment      TEXT NOT NULL,
    offline_at       TEXT NOT NULL,               -- ISO-8601 UTC
    recovered_at     TEXT,                         -- NULL while open
    downtime_seconds REAL,                         -- computed on close
    FOREIGN KEY (bot_id, environment) REFERENCES bots (bot_id, environment)
);

CREATE INDEX IF NOT EXISTS idx_incidents_bot ON incidents (bot_id, environment);
CREATE INDEX IF NOT EXISTS idx_incidents_active ON incidents (recovered_at) WHERE recovered_at IS NULL;
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA_SQL)
        await db.commit()


# ──────────────────────────────────────────────────────────────────
# Bot Repository
# ──────────────────────────────────────────────────────────────────
class SqliteBotRepository(IBotRepository):

    def _row_to_bot(self, row) -> Bot:
        return Bot(
            bot_id=row[0],
            name=row[1],
            environment=BotEnvironment(row[2]),
            status=BotStatus(row[3]),
            last_seen=datetime.fromisoformat(row[4]) if row[4] else None,
            registered_at=datetime.fromisoformat(row[5]),
        )

    async def upsert(self, bot: Bot) -> Bot:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO bots (bot_id, name, environment, status, last_seen, registered_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (bot_id, environment) DO UPDATE SET
                    name        = excluded.name,
                    status      = excluded.status,
                    last_seen   = excluded.last_seen
                """,
                (
                    bot.bot_id,
                    bot.name,
                    bot.environment.value,
                    bot.status.value,
                    bot.last_seen.isoformat() if bot.last_seen else None,
                    bot.registered_at.isoformat(),
                ),
            )
            await db.commit()
        return bot

    async def find_by_id(self, bot_id: str, environment: str) -> Optional[Bot]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT bot_id, name, environment, status, last_seen, registered_at "
                "FROM bots WHERE bot_id = ? AND environment = ?",
                (bot_id, environment),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_bot(row) if row else None

    async def find_all(self) -> List[Bot]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT bot_id, name, environment, status, last_seen, registered_at FROM bots ORDER BY environment, bot_id"
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_bot(r) for r in rows]

    async def update_status(self, bot_id: str, environment: str, status: str) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE bots SET status = ? WHERE bot_id = ? AND environment = ?",
                (status, bot_id, environment),
            )
            await db.commit()


# ──────────────────────────────────────────────────────────────────
# Incident Repository
# ──────────────────────────────────────────────────────────────────
class SqliteIncidentRepository(IIncidentRepository):

    def _row_to_incident(self, row) -> Incident:
        return Incident(
            incident_id=row[0],
            bot_id=row[1],
            environment=BotEnvironment(row[2]),
            offline_at=datetime.fromisoformat(row[3]),
            recovered_at=datetime.fromisoformat(row[4]) if row[4] else None,
            downtime_seconds=row[5],
        )

    async def open_incident(self, incident: Incident) -> Incident:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO incidents (incident_id, bot_id, environment, offline_at) VALUES (?, ?, ?, ?)",
                (incident.incident_id, incident.bot_id, incident.environment.value, incident.offline_at.isoformat()),
            )
            await db.commit()
        return incident

    async def close_incident(self, bot_id: str, environment: str, recovered_at: datetime) -> Optional[Incident]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT incident_id, bot_id, environment, offline_at, recovered_at, downtime_seconds "
                "FROM incidents WHERE bot_id = ? AND environment = ? AND recovered_at IS NULL "
                "ORDER BY offline_at DESC LIMIT 1",
                (bot_id, environment),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                return None

            incident = self._row_to_incident(row)
            incident.resolve(recovered_at)

            await db.execute(
                "UPDATE incidents SET recovered_at = ?, downtime_seconds = ? WHERE incident_id = ?",
                (incident.recovered_at.isoformat(), incident.downtime_seconds, incident.incident_id),
            )
            await db.commit()
        return incident

    async def find_active_incident(self, bot_id: str, environment: str) -> Optional[Incident]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT incident_id, bot_id, environment, offline_at, recovered_at, downtime_seconds "
                "FROM incidents WHERE bot_id = ? AND environment = ? AND recovered_at IS NULL",
                (bot_id, environment),
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_incident(row) if row else None

    async def find_all(self, limit: int = 100) -> List[Incident]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT incident_id, bot_id, environment, offline_at, recovered_at, downtime_seconds "
                "FROM incidents ORDER BY offline_at DESC LIMIT ?",
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_incident(r) for r in rows]
