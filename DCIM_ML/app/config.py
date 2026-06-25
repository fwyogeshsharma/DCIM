"""Environment-driven configuration (mirrors docker-compose env block)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Database (shared TimescaleDB) ──────────────────────────────────────────
    postgres_host: str = "timescaledb"
    postgres_port: int = 5432
    postgres_db: str = "dcim_aggregator"
    postgres_user: str = "dcim"
    postgres_password: str = "dcim_pas"

    # ── Service ────────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    model_path: str = "/app/models"

    # ── Capacity assumptions ──────────────────────────────────────────────────
    rack_u_default: int = 42
    rack_power_capacity_w: int = 10000

    # ── Forecasting ────────────────────────────────────────────────────────────
    forecast_horizons: list[int] = [30, 60, 90]
    cpu_threshold_pct: float = 85.0
    mem_threshold_pct: float = 85.0
    rack_warn_pct: float = 95.0

    # ── Retraining ─────────────────────────────────────────────────────────────
    retrain_cron: str = "0 2 * * *"
    run_on_startup: bool = True
    stale_run_hours: int = 20

    @field_validator("forecast_horizons", mode="before")
    @classmethod
    def _parse_horizons(cls, v):
        if isinstance(v, str):
            return [int(x) for x in v.split(",") if x.strip()]
        return v

    @property
    def sqlalchemy_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
