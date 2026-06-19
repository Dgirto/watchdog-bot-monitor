"""RecordHealthUseCase: sanitization + anomaly detection."""
from domain.entities.bot import BotEnvironment
from domain.entities.health import HealthMetrics
from use_cases.health import RecordHealthUseCase


def test_sanitize_keeps_known_numeric_only():
    clean = RecordHealthUseCase._sanitize({
        "inference_latency_p95_ms": 820.0,
        "queue_depth": 5,
        "llm_error_rate": 0.01,
        "evil": "<script>",            # junk string -> dropped
        "queue_depth_bool": True,      # bool -> not a known field anyway
        "tokens_per_sec": 47.3,        # removed metric -> dropped
        "session_cost_usd": 1.0,       # removed metric -> dropped
        "unknown_field": 1,            # not in allow-list -> dropped
    })
    assert clean == {"inference_latency_p95_ms": 820.0, "queue_depth": 5, "llm_error_rate": 0.01}


def test_sanitize_drops_bools():
    clean = RecordHealthUseCase._sanitize({"queue_depth": True, "llm_error_rate": 0.1})
    assert clean == {"llm_error_rate": 0.1}


def test_detect_anomalies_flags_error_rate_and_queue():
    m = HealthMetrics(
        bot_id="b", environment=BotEnvironment.PROD,
        llm_error_rate=0.2, queue_depth=300,
    )
    flags = RecordHealthUseCase.detect_anomalies(m)
    assert any("error rate" in f for f in flags)
    assert any("queue" in f for f in flags)


def test_detect_anomalies_clean_when_healthy():
    m = HealthMetrics(
        bot_id="b", environment=BotEnvironment.PROD,
        llm_error_rate=0.01, queue_depth=3,
    )
    assert RecordHealthUseCase.detect_anomalies(m) == []


class _FakeHealthRepo:
    def __init__(self):
        self.saved = []

    async def save(self, m):
        self.saved.append(m)

    async def find_recent(self, *a, **k):
        return self.saved

    async def find_latest_all(self):
        return self.saved


async def test_execute_saves_sanitized_metrics():
    repo = _FakeHealthRepo()
    uc = RecordHealthUseCase(repo)
    m = await uc.execute("agent-1", "prod", {"inference_latency_p95_ms": 500.0, "junk": "x"})
    assert m.inference_latency_p95_ms == 500.0
    assert repo.saved[0].bot_id == "agent-1"
    assert repo.saved[0].environment == BotEnvironment.PROD
