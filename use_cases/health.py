"""
use_cases/health.py — record AI-agent health metrics reported over WebSocket.
"""
import logging
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
    def _sanitize(raw: dict) -> dict:
        """Keep only known numeric fields; drop junk an attacker might inject."""
        clean: dict = {}
        for key in _ALLOWED:
            if key in raw and isinstance(raw[key], (int, float)) and not isinstance(raw[key], bool):
                clean[key] = raw[key]
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
