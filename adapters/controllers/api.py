"""
adapters/controllers/api.py
HTTP layer — thin. Parse → Use Case → Respond.
"""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator

from infrastructure.container import container

router = APIRouter()


# ─────────────────── Pydantic I/O schemas ────────────────────────

VALID_ENVS = {"prod", "staging", "dev"}


class HeartbeatIn(BaseModel):
    bot_id: str
    environment: str
    name: Optional[str] = None

    @field_validator("environment")
    @classmethod
    def validate_env(cls, v: str) -> str:
        if v not in VALID_ENVS:
            raise ValueError(f"environment must be one of {VALID_ENVS}")
        return v

    @field_validator("bot_id")
    @classmethod
    def validate_bot_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("bot_id cannot be empty")
        return v


class HeartbeatOut(BaseModel):
    bot_id: str
    environment: str
    status: str
    registered: bool
    timestamp: datetime


class BotOut(BaseModel):
    bot_id: str
    name: str
    environment: str
    status: str
    last_seen: Optional[datetime]
    registered_at: datetime


class IncidentOut(BaseModel):
    incident_id: str
    bot_id: str
    environment: str
    offline_at: datetime
    recovered_at: Optional[datetime]
    downtime_seconds: Optional[float]


class FleetStatusOut(BaseModel):
    generated_at: datetime
    total: int
    online: int
    offline: int
    unknown: int
    bots: List[BotOut]
    recent_incidents: List[IncidentOut]


# ─────────────────────────── Routes ──────────────────────────────

@router.post(
    "/heartbeat",
    response_model=HeartbeatOut,
    status_code=status.HTTP_200_OK,
    summary="Receive a heartbeat signal from a bot",
    tags=["Heartbeat"],
)
async def heartbeat(payload: HeartbeatIn) -> HeartbeatOut:
    from use_cases.watchdog import HeartbeatRequest
    request = HeartbeatRequest(
        bot_id=payload.bot_id,
        environment=payload.environment,
        name=payload.name,
    )
    result = await container.process_heartbeat.execute(request)
    return HeartbeatOut(
        bot_id=result.bot_id,
        environment=result.environment,
        status=result.status,
        registered=result.registered,
        timestamp=result.timestamp,
    )


@router.get(
    "/status",
    response_model=FleetStatusOut,
    summary="Full fleet availability status",
    tags=["Monitoring"],
)
async def fleet_status() -> FleetStatusOut:
    bots = await container.bot_repo.find_all()
    incidents = await container.incident_repo.find_all(limit=50)

    counts = {"online": 0, "offline": 0, "unknown": 0}
    for b in bots:
        counts[b.status.value] = counts.get(b.status.value, 0) + 1

    return FleetStatusOut(
        generated_at=datetime.utcnow(),
        total=len(bots),
        **counts,
        bots=[
            BotOut(
                bot_id=b.bot_id,
                name=b.name,
                environment=b.environment.value,
                status=b.status.value,
                last_seen=b.last_seen,
                registered_at=b.registered_at,
            )
            for b in bots
        ],
        recent_incidents=[
            IncidentOut(
                incident_id=i.incident_id,
                bot_id=i.bot_id,
                environment=i.environment.value,
                offline_at=i.offline_at,
                recovered_at=i.recovered_at,
                downtime_seconds=i.downtime_seconds,
            )
            for i in incidents
        ],
    )
