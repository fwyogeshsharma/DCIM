"""APScheduler wiring: daily retrain + an optional catch-up run at startup."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import writeback as wb
from app.db.engine import get_engine
from app.logging_conf import get_logger
from app.pipeline.train import run_pipeline

log = get_logger("dcim_ml.scheduler")

_scheduler: BackgroundScheduler | None = None
_run_lock = threading.Lock()


def run_pipeline_guarded() -> dict | None:
    """Run the pipeline, ensuring only one pass executes at a time."""
    if not _run_lock.acquire(blocking=False):
        log.warning("pipeline_already_running")
        return None
    try:
        return run_pipeline()
    finally:
        _run_lock.release()


def _last_run_is_stale() -> bool:
    s = get_settings()
    try:
        with get_engine().connect() as conn:
            last = wb.last_run_at(conn)
    except Exception as exc:  # noqa: BLE001
        log.warning("last_run_check_failed", error=str(exc))
        return True
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last > timedelta(hours=s.stale_run_hours)


def start_scheduler() -> None:
    global _scheduler
    s = get_settings()
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_pipeline_guarded,
        CronTrigger.from_crontab(s.retrain_cron, timezone="UTC"),
        id="daily_retrain", max_instances=1, coalesce=True,
    )
    _scheduler.start()
    log.info("scheduler_started", cron=s.retrain_cron)

    if s.run_on_startup and _last_run_is_stale():
        log.info("startup_run_scheduled")
        threading.Thread(target=run_pipeline_guarded, daemon=True).start()


def shutdown_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
