"""SQLAlchemy engine + schema bootstrap for the shared TimescaleDB.

DCIM_ML reads existing aggregator tables (SELECT only) and writes its own
``ml_*`` tables. The engine is a module-level singleton with a small pool.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.logging_conf import get_logger

log = get_logger("dcim_ml.db")

_engine: Engine | None = None

_SCHEMA_FILE = Path(__file__).parent / "ml_schema.sql"


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_engine(
            s.sqlalchemy_url,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            pool_recycle=1800,
            future=True,
        )
    return _engine


def ping() -> bool:
    """Return True if the database is reachable."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("db_ping_failed", error=str(exc))
        return False


def ensure_ml_schema() -> None:
    """Create the ``ml_*`` tables if they don't exist (idempotent)."""
    sql = _SCHEMA_FILE.read_text(encoding="utf-8")
    # exec_driver_sql sends the raw string to psycopg as a simple query, which
    # supports multiple ';'-separated statements (there are no bound params here).
    with get_engine().begin() as conn:
        conn.exec_driver_sql(sql)
    log.info("ml_schema_ensured")
