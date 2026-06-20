"""
use_cases/health.py — record AI-agent health metrics reported over WebSocket.
"""
import logging
import math
from typing import List

from domain.entities.bot import BotEnvironment
from domain.entities.health import HealthMetrics
from domain.interfaces.repositories import IHealthRepository

logger = logging.getLogger("watchdog.health")

# Fields we accept from an agent's `health` message; anything else is ignored.
_ALLOWED = {
    "inference_latency_p95_ms",
    "llm_error_rate",
    "queue_depth",
}


class RecordHealthUseCase:
    def __init__(self, health_repo: IHealthRepository):
        self._health = health_repo

    @staticmethod
    def _sanitize(raw) -> dict:
        """Keep only known, finite numeric fields. Hostile inputs (a non-dict
        payload, booleans, NaN/Infinity, junk keys) are silently dropped — they
        must never crash the handler nor poison the JSON API response."""
        if not isinstance(raw, dict):
            return {}
        clean: dict = {}
        for key in _ALLOWED:
            value = raw.get(key)
            if isinstance(value, bool):  # bool is a subclass of int — exclude
                continue
            if isinstance(value, (int, float)) and math.isfinite(value):
                clean[key] = value
        return clean

    @staticmethod
    def detect_anomalies(metrics: HealthMetrics) -> List[str]:
        """Cheap, stateless red flags — an agent can be online yet unhealthy."""
        flags: List[str] = []
        if metrics.llm_error_rate is not None and metrics.llm_error_rate > 0.05:
            flags.append(f"high LLM error rate ({metrics.llm_error_rate:.0%})")
        if metrics.queue_depth is not None and metrics.queue_depth > 200:
            flags.append(f"queue backing up ({metrics.queue_depth})")
        return flags

    async def execute(self, bot_id: str, environment: str, raw_metrics: dict) -> HealthMetrics:
        metrics = HealthMetrics(
            bot_id=bot_id,
            environment=BotEnvironment(environment),
            **self._sanitize(raw_metrics),
        )
        await self._health.save(metrics)

        for flag in self.detect_anomalies(metrics):
            logger.warning("Health anomaly | bot_id=%s env=%s — %s", bot_id, environment, flag)

        return metrics
