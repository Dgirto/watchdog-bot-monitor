"""RecordHealthUseCase: sanitization + anomaly detection."""
from domain.entities.bot import BotEnvironment
from domain.entities.health import HealthMetrics
from use_cases.health import RecordHealthUseCase


def test_sanitize_keeps_known_numeric_only():
    clean = RecordHealthUseCase._sanitize({
        "tokens_per_sec": 47.3,
        "queue_depth": 5,
        "llm_error_rate": 0.01,
        "evil": "<script>",        # junk string -> dropped
        "session_cost_usd": True,  # bool -> dropped (not a real number)
        "unknown_field": 1,        # not in allow-list -> dropped
    })
    assert clean == {"tokens_per_sec": 47.3, "queue_depth": 5, "llm_error_rate": 0.01}


def test_detect_anomalies_flags_error_rate_and_cost():
    m = HealthMetrics(
        bot_id="b", environment=BotEnvironment.PROD,
        llm_error_rate=0.2, session_cost_usd=100.0,
    )
    flags = RecordHealthUseCase.detect_anomalies(m)
    assert any("error rate" in f for f in flags)
    assert any("cost" in f for f in flags)


def test_detect_anomalies_clean_when_healthy():
    m = HealthMetrics(
        bot_id="b", environment=BotEnvironment.PROD,
        llm_error_rate=0.01, session_cost_usd=1.0,
    )
    assert RecordHealthUseCase.detect_anomalies(m) == []


class _FakeHealthRepo:
    def __init__(self):
        self.saved = []

    async def save(self, m):
        self.saved.append(m)

    async def find_recent(self, *a, **k):
        return self.saved


async def test_execute_saves_sanitized_metrics():
    repo = _FakeHealthRepo()
    uc = RecordHealthUseCase(repo)
    m = await uc.execute("agent-1", "prod", {"tokens_per_sec": 10.0, "junk": "x"})
    assert m.tokens_per_sec == 10.0
    assert repo.saved[0].bot_id == "agent-1"
    assert repo.saved[0].environment == BotEnvironment.PROD
