"""DCIM_ML FastAPI entrypoint.

On startup: ensure the ml_* tables exist and start the retrain scheduler.
On shutdown: stop the scheduler.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.routes import router
from app.config import get_settings
from app.db.engine import ensure_ml_schema, ping
from app.logging_conf import configure_logging, get_logger
from app.pipeline.scheduler import shutdown_scheduler, start_scheduler

settings = get_settings()
configure_logging(settings.log_level)
log = get_logger("dcim_ml.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", version=__version__)
    if ping():
        try:
            ensure_ml_schema()
            start_scheduler()
        except Exception as exc:  # noqa: BLE001
            log.error("startup_init_failed", error=str(exc))
    else:
        log.warning("startup_db_unreachable")
    yield
    shutdown_scheduler()
    log.info("shutdown")


app = FastAPI(
    title="DCIM_ML — AI Capacity Planning & Rack Forecasting",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(router)
