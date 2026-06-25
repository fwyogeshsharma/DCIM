"""FastAPI routes — read the persisted ml_* tables, plus a manual train trigger."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.api import schemas
from app.db import writeback as wb
from app.db.engine import get_engine, ping
from app.logging_conf import get_logger
from app.pipeline.scheduler import run_pipeline_guarded

log = get_logger("dcim_ml.api")
router = APIRouter()


@router.get("/health", response_model=schemas.HealthResponse, tags=["meta"])
def health() -> schemas.HealthResponse:
    db_ok = ping()
    last = None
    if db_ok:
        try:
            with get_engine().connect() as conn:
                last = wb.last_run_at(conn)
        except Exception:  # noqa: BLE001
            pass
    return schemas.HealthResponse(
        status="healthy" if db_ok else "degraded", db_reachable=db_ok, last_run_at=last
    )


@router.get("/api/v1/forecasts", response_model=list[schemas.ForecastPointOut], tags=["forecasts"])
def get_forecasts(
    scope: str | None = Query(None),
    scope_id: str | None = Query(None),
    target: str | None = Query(None),
    horizon: int | None = Query(None, description="Only points within N days from now"),
    limit: int = Query(2000, le=10000),
):
    clauses, params = [], {}
    if scope:
        clauses.append("scope = :scope"); params["scope"] = scope
    if scope_id:
        clauses.append("scope_id = :scope_id"); params["scope_id"] = scope_id
    if target:
        clauses.append("target = :target"); params["target"] = target
    if horizon:
        clauses.append("forecast_for <= now() + make_interval(days => :h)"); params["h"] = horizon
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params["lim"] = limit
    sql = (
        "SELECT scope, scope_id, target, forecast_for, predicted_value, lower_bound, "
        "upper_bound, unit, model_name, model_version, generated_at "
        f"FROM ml_forecasts{where} ORDER BY scope, scope_id, target, forecast_for LIMIT :lim"
    )
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [schemas.ForecastPointOut(**r) for r in rows]


@router.get("/api/v1/capacity/summary", response_model=list[schemas.CapacitySummaryOut], tags=["capacity"])
def get_capacity_summary(scope: str | None = Query(None), scope_id: str | None = Query(None)):
    clauses, params = [], {}
    if scope:
        clauses.append("scope = :scope"); params["scope"] = scope
    if scope_id:
        clauses.append("scope_id = :scope_id"); params["scope_id"] = scope_id
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(f"SELECT * FROM ml_capacity_summary{where} ORDER BY scope, scope_id"), params
        ).mappings().all()
    return [schemas.CapacitySummaryOut(**r) for r in rows]


@router.get("/api/v1/recommendations", response_model=list[schemas.RecommendationOut], tags=["recommendations"])
def get_recommendations(
    scope: str | None = Query(None),
    scope_id: str | None = Query(None),
    severity: str | None = Query(None),
    status: str = Query("open"),
    limit: int = Query(500, le=5000),
):
    clauses, params = ["status = :status"], {"status": status}
    if scope:
        clauses.append("scope = :scope"); params["scope"] = scope
    if scope_id:
        clauses.append("scope_id = :scope_id"); params["scope_id"] = scope_id
    if severity:
        clauses.append("severity = :severity"); params["severity"] = severity
    params["lim"] = limit
    where = " WHERE " + " AND ".join(clauses)
    sql = (
        "SELECT id, scope, scope_id, category, severity, message, confidence, "
        "predicted_date, details, status, created_at "
        f"FROM ml_recommendations{where} "
        "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, "
        "created_at DESC LIMIT :lim"
    )
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [schemas.RecommendationOut(**r) for r in rows]


@router.get("/api/v1/models", response_model=list[schemas.ModelRunOut], tags=["models"])
def get_model_runs(limit: int = Query(200, le=2000)):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT scope, scope_id, target, model_name, model_version, n_points, "
                "mae, mape, accepted, trained_at FROM ml_model_runs "
                "ORDER BY trained_at DESC LIMIT :lim"
            ),
            {"lim": limit},
        ).mappings().all()
    return [schemas.ModelRunOut(**r) for r in rows]


@router.post("/api/v1/train", response_model=schemas.TrainResponse, tags=["pipeline"])
def trigger_train():
    result = run_pipeline_guarded()
    if result is None:
        raise HTTPException(status_code=409, detail="A training run is already in progress")
    return schemas.TrainResponse(
        run_id=result["run_id"], scopes=result["scopes"],
        forecasts=result["forecasts"], recommendations=result["recommendations"],
    )
