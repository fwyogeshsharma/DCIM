"""Pydantic response models for the DCIM_ML API."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str = "DCIM_ML"
    db_reachable: bool
    last_run_at: datetime | None = None


class ForecastPointOut(BaseModel):
    scope: str
    scope_id: str
    target: str
    forecast_for: datetime
    predicted_value: float
    lower_bound: float | None
    upper_bound: float | None
    unit: str | None
    model_name: str | None
    model_version: str | None
    generated_at: datetime


class CapacitySummaryOut(BaseModel):
    scope: str
    scope_id: str
    days_until_rack_full: int | None
    rack_exhaustion_date: date | None
    days_until_storage_full: int | None
    storage_exhaustion_date: date | None
    predicted_new_racks_30d: int | None
    predicted_new_racks_60d: int | None
    predicted_new_racks_90d: int | None
    expected_server_growth_30d: int | None
    expected_server_growth_60d: int | None
    expected_server_growth_90d: int | None
    power_headroom_pct: float | None
    details: dict
    updated_at: datetime


class RecommendationOut(BaseModel):
    id: int
    scope: str
    scope_id: str
    category: str
    severity: str
    message: str
    confidence: float
    predicted_date: date | None
    details: dict
    status: str
    created_at: datetime


class ModelRunOut(BaseModel):
    scope: str
    scope_id: str
    target: str
    model_name: str
    model_version: str | None
    n_points: int | None
    mae: float | None
    mape: float | None
    accepted: bool
    trained_at: datetime


class TrainResponse(BaseModel):
    run_id: str
    scopes: int
    forecasts: int
    recommendations: int
