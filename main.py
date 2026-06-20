"""
main.py — application entrypoint
Run with: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from adapters.controllers.api import router
from adapters.controllers.dashboard import dashboard_router
from adapters.controllers.ws import ws_router
from infrastructure.container import container
from infrastructure.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("watchdog.main")


# ──────────────────────────────────────────────────────────────────
# Background watchdog task
# ──────────────────────────────────────────────────────────────────
async def _watchdog_loop() -> None:
    logger.info(
        "Watchdog sweep started — timeout=%ds grace=%ds interval=%ds",
        settings.HEARTBEAT_TIMEOUT_SECONDS,
        settings.GRACE_PERIOD_SECONDS,
        settings.WATCHDOG_INTERVAL_SECONDS,
    )
    while True:
        try:
            newly_offline = await container.run_watchdog.execute()
            if newly_offline:
                logger.warning("Watchdog marked %d bot(s) as OFFLINE", newly_offline)
        except Exception as exc:  # noqa: BLE001
            logger.error("Watchdog sweep error: %s", exc)
        await asyncio.sleep(settings.WATCHDOG_INTERVAL_SECONDS)


# ──────────────────────────────────────────────────────────────────
# App lifespan: startup / shutdown
# ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — fail fast on bad configuration, surface risky one.
    errors = settings.validate()
    if errors:
        for err in errors:
            logger.error("CONFIG ERROR: %s", err)
        raise RuntimeError("Invalid configuration: " + "; ".join(errors))
    for warn in settings.warnings():
        logger.warning("CONFIG: %s", warn)

    await container.init_db()
    logger.info("Database initialised (backend=%s)", settings.DB_BACKEND)
    task = asyncio.create_task(_watchdog_loop())
    logger.info("Watchdog background task started")
    yield
    # Shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Watchdog shut down cleanly")


# ──────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Watchdog — Bot Availability Monitor",
    description=(
        "Lightweight uptime monitoring for production bots. "
        "Tracks heartbeats, detects outages, logs incidents, and sends alerts."
    ),
    version="1.0.0",
    lifespan=lifespan,
    # API docs leak the full surface — disabled in prod unless EXPOSE_DOCS=true.
    docs_url="/docs" if settings.DOCS_ENABLED else None,
    redoc_url="/redoc" if settings.DOCS_ENABLED else None,
    openapi_url="/openapi.json" if settings.DOCS_ENABLED else None,
)


@app.middleware("http")
async def _security_headers(request, call_next):
    """Baseline hardening for every HTTP response (esp. the dashboard)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


app.include_router(router)
app.include_router(dashboard_router)
app.include_router(ws_router)


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}
