"""
adapters/controllers/api.py
HTTP layer — thin. Parse → Use Case → Respond.
"""
import re
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, field_validator

from adapters.controllers.auth import verify_heartbeat_auth
from infrastructure.container import container
from infrastructure.time import utcnow

router = APIRouter()


# ─────────────────── Pydantic I/O schemas ────────────────────────

VALID_ENVS = {"prod", "staging", "dev"}

# Identifiers must be safe to render and store — blocks XSS payloads at the door (S-3).
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_NAME_RE = re.compile(r"^[\w .\-]{1,80}$", re.UNICODE)


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
        if not _ID_RE.match(v):
            raise ValueError("bot_id must be 1-64 chars: letters, digits, . _ -")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not _NAME_RE.match(v):
            raise ValueError("name must be 1-80 chars: letters, digits, spaces, . _ -")
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


class HealthMetricsOut(BaseModel):
    bot_id: str
    environment: str
    recorded_at: datetime
    inference_latency_p95_ms: Optional[float] = None
    tokens_per_sec: Optional[float] = None
    llm_error_rate: Optional[float] = None
    session_cost_usd: Optional[float] = None
    queue_depth: Optional[int] = None


# ─────────────────────────── Routes ──────────────────────────────

@router.post(
    "/heartbeat",
    response_model=HeartbeatOut,
    status_code=status.HTTP_200_OK,
    summary="Receive a heartbeat signal from a bot",
    tags=["Heartbeat"],
)
async def heartbeat(
    payload: HeartbeatIn,
    x_timestamp: Optional[str] = Header(default=None),
    x_signature: Optional[str] = Header(default=None),
) -> HeartbeatOut:
    # Verify the agent is who it claims to be before recording anything (S-2).
    verify_heartbeat_auth(payload.bot_id, payload.environment, x_timestamp, x_signature)

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
        generated_at=utcnow(),
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


@router.get(
    "/agents/{bot_id}/health",
    response_model=List[HealthMetricsOut],
    summary="Recent AI health metrics for an agent",
    tags=["Monitoring"],
)
async def agent_health(bot_id: str, environment: str, limit: int = 50) -> List[HealthMetricsOut]:
    if environment not in VALID_ENVS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid environment")
    metrics = await container.health_repo.find_recent(bot_id, environment, limit=min(limit, 200))
    return [
        HealthMetricsOut(
            bot_id=m.bot_id,
            environment=m.environment.value,
            recorded_at=m.recorded_at,
            inference_latency_p95_ms=m.inference_latency_p95_ms,
            tokens_per_sec=m.tokens_per_sec,
            llm_error_rate=m.llm_error_rate,
            session_cost_usd=m.session_cost_usd,
            queue_depth=m.queue_depth,
        )
        for m in metrics
    ]
