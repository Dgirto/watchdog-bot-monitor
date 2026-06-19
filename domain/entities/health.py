"""
domain/entities/health.py
AI-agent health metrics — what makes an "AI agent" different from a generic bot.

A generic bot is alive or dead. An AI agent can be *alive but degraded or
burning money*: serving traffic while the LLM errors out, latency spikes, or
the session cost runs away. These metrics capture that.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from domain.entities.bot import BotEnvironment


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class HealthMetrics:
    bot_id: str
    environment: BotEnvironment
    recorded_at: datetime = None  # type: ignore[assignment]

    inference_latency_p95_ms: Optional[float] = None  # LLM responsiveness
    tokens_per_sec: Optional[float] = None            # useful throughput
    llm_error_rate: Optional[float] = None            # 0..1 — alive but failing
    session_cost_usd: Optional[float] = None          # runaway-cost guard
    queue_depth: Optional[int] = None                 # backpressure / saturation

    def __post_init__(self):
        if self.recorded_at is None:
            self.recorded_at = _utcnow()
