"""End-to-end retraining pipeline (spec section 13).

For each scope (global + per datacenter):
  pull series → select/fit forecasters → derive capacity → build recommendations
  → write forecasts / capacity_summary / recommendations / model_runs (one txn).

Everything degrades gracefully: a scope with no inventory or no series is
skipped; a target with too little history falls back to the trend model.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pandas as pd

from app.config import get_settings
from app.db import source_queries as sq
from app.db import writeback as wb
from app.db.engine import get_engine
from app.db.source_queries import TARGET_UNITS
from app.engines.capacity import compute_capacity
from app.engines.recommendations import build_recommendations
from app.logging_conf import get_logger
from app.models import registry
from app.models.base import ForecastPoint
from app.models.selection import Selection, select_forecaster

log = get_logger("dcim_ml.pipeline")

# Targets forecast directly from a real/observed series.
DIRECT_TARGETS = ["cpu", "memory", "power", "server_count"]


def _points_to_rows(scope, scope_id, target, points, unit, model_name) -> list[dict]:
    return [
        {
            "scope": scope, "scope_id": scope_id, "target": target,
            "forecast_for": p.forecast_for, "predicted_value": p.value,
            "lower_bound": p.lower, "upper_bound": p.upper, "unit": unit,
            "model_name": model_name, "model_version": registry.MODEL_VERSION,
        }
        for p in points
    ]


def _flat_points(value: float, last_date: pd.Timestamp, horizon: int) -> list[ForecastPoint]:
    idx = pd.date_range(last_date + pd.Timedelta(days=1), periods=horizon, freq="D")
    return [ForecastPoint(d.to_pydatetime(), value, value, value) for d in idx]


def _series_for(conn, target, dc):
    if target == "power":
        return sq.fetch_power_series(conn, dc)
    if target == "server_count":
        return sq.fetch_server_count_series(conn, dc)
    return sq.fetch_metric_series(conn, target, dc)


def _run_scope(conn, scope: str, scope_id: str, dc: str | None,
               run_id: str, today: date) -> dict:
    s = get_settings()
    horizon = max(s.forecast_horizons)

    forecasts: list[dict] = []
    model_runs: list[dict] = []
    selections: dict[str, Selection] = {}

    for target in DIRECT_TARGETS:
        series = _series_for(conn, target, dc)
        sel = select_forecaster(series, horizon)
        if sel is None:
            continue
        selections[target] = sel
        registry.save(scope, scope_id, target, sel.model)
        forecasts += _points_to_rows(scope, scope_id, target, sel.points,
                                     TARGET_UNITS.get(target), sel.name)
        model_runs.append({
            "scope": scope, "scope_id": scope_id, "target": target,
            "model_name": sel.name, "model_version": registry.MODEL_VERSION,
            "n_points": sel.n_points, "mae": sel.mae, "mape": sel.mape,
            "accepted": True, "notes": None,
        })

    # ── Capacity (needs a server-growth curve; synthesize a flat one if absent) ─
    snapshot = sq.fetch_capacity_snapshot(conn, dc)
    active_devices = snapshot.get("totals", {}).get("active_devices") or 0
    current_servers = _last_observed(_series_for(conn, "server_count", dc),
                                     default=active_devices)
    server_sel = selections.get("server_count")
    if server_sel is not None:
        server_points = server_sel.points
    else:
        server_points = _flat_points(current_servers, pd.Timestamp(today), horizon)

    power_points = selections["power"].points if "power" in selections else None

    cap = compute_capacity(
        snapshot, server_points, current_servers, power_points,
        rack_u_default=s.rack_u_default, rack_power_capacity_w=s.rack_power_capacity_w,
        horizons=s.forecast_horizons, today=today,
    )

    # Derived forecast curves (rack_units, storage in GB).
    for target, pts in cap.derived_forecasts.items():
        forecasts += _points_to_rows(scope, scope_id, target, pts,
                                     TARGET_UNITS.get(target), "derived")
        model_runs.append({
            "scope": scope, "scope_id": scope_id, "target": target,
            "model_name": "derived", "model_version": registry.MODEL_VERSION,
            "n_points": len(pts), "mae": None, "mape": None,
            "accepted": True, "notes": "derived from server-growth forecast",
        })

    cap_row = {"scope": scope, "scope_id": scope_id, **cap.summary}

    recs = build_recommendations(
        scope, scope_id, cap.summary, selections, today=today,
        cpu_threshold=s.cpu_threshold_pct, mem_threshold=s.mem_threshold_pct,
        server_mape=server_sel.mape if server_sel else None,
    )

    wb.write_forecasts(conn, run_id, forecasts)
    wb.upsert_capacity_summary(conn, run_id, [cap_row])
    wb.write_recommendations(conn, run_id, recs)
    wb.write_model_runs(conn, run_id, model_runs)

    return {"scope": scope_id, "forecasts": len(forecasts),
            "recommendations": len(recs), "targets": len(model_runs)}


def _last_observed(series: pd.Series, default=0) -> float:
    s = series.dropna()
    return float(s.iloc[-1]) if len(s) else float(default)


def run_pipeline() -> dict:
    """Run a full training/forecasting pass across all scopes."""
    run_id = str(uuid.uuid4())
    today = datetime.now(timezone.utc).date()
    log.info("pipeline_start", run_id=run_id)

    scopes: list[tuple[str, str, str | None]] = [("global", "all", None)]
    with get_engine().connect() as conn:
        for dc in sq.list_datacenters(conn):
            scopes.append(("datacenter", dc, dc))

    results = []
    for scope, scope_id, dc in scopes:
        try:
            with get_engine().begin() as conn:  # one transaction per scope
                results.append(_run_scope(conn, scope, scope_id, dc, run_id, today))
        except Exception as exc:  # noqa: BLE001
            log.error("scope_failed", scope=scope_id, error=str(exc))

    summary = {
        "run_id": run_id,
        "scopes": len(results),
        "forecasts": sum(r["forecasts"] for r in results),
        "recommendations": sum(r["recommendations"] for r in results),
        "details": results,
    }
    log.info("pipeline_done", **{k: v for k, v in summary.items() if k != "details"})
    return summary
