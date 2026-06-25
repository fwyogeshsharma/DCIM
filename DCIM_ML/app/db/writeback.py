"""Write forecasts / capacity / recommendations / run-audit into ml_* tables.

A pipeline run regenerates everything for the scopes it touches, so we replace
the prior rows for those scopes rather than accumulating duplicates. `ack`/
`resolved` recommendations are preserved; only stale `open` ones are cleared.
"""
from __future__ import annotations

import json
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.logging_conf import get_logger

log = get_logger("dcim_ml.writeback")


def write_forecasts(conn: Connection, run_id: str, points: list[dict]) -> int:
    if not points:
        return 0
    keys = {(p["scope"], p["scope_id"], p["target"]) for p in points}
    for scope, scope_id, target in keys:
        conn.execute(
            text(
                "DELETE FROM ml_forecasts WHERE scope=:s AND scope_id=:sid AND target=:t"
            ),
            {"s": scope, "sid": scope_id, "t": target},
        )
    conn.execute(
        text(
            """
            INSERT INTO ml_forecasts
                (run_id, scope, scope_id, target, forecast_for, predicted_value,
                 lower_bound, upper_bound, unit, model_name, model_version)
            VALUES
                (:run_id, :scope, :scope_id, :target, :forecast_for, :predicted_value,
                 :lower_bound, :upper_bound, :unit, :model_name, :model_version)
            """
        ),
        [{"run_id": run_id, **p} for p in points],
    )
    return len(points)


def upsert_capacity_summary(conn: Connection, run_id: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    conn.execute(
        text(
            """
            INSERT INTO ml_capacity_summary (
                scope, scope_id, run_id,
                days_until_rack_full, rack_exhaustion_date,
                days_until_storage_full, storage_exhaustion_date,
                predicted_new_racks_30d, predicted_new_racks_60d, predicted_new_racks_90d,
                expected_server_growth_30d, expected_server_growth_60d, expected_server_growth_90d,
                power_headroom_pct, details, updated_at
            ) VALUES (
                :scope, :scope_id, :run_id,
                :days_until_rack_full, :rack_exhaustion_date,
                :days_until_storage_full, :storage_exhaustion_date,
                :predicted_new_racks_30d, :predicted_new_racks_60d, :predicted_new_racks_90d,
                :expected_server_growth_30d, :expected_server_growth_60d, :expected_server_growth_90d,
                :power_headroom_pct, CAST(:details AS JSONB), now()
            )
            ON CONFLICT (scope, scope_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                days_until_rack_full = EXCLUDED.days_until_rack_full,
                rack_exhaustion_date = EXCLUDED.rack_exhaustion_date,
                days_until_storage_full = EXCLUDED.days_until_storage_full,
                storage_exhaustion_date = EXCLUDED.storage_exhaustion_date,
                predicted_new_racks_30d = EXCLUDED.predicted_new_racks_30d,
                predicted_new_racks_60d = EXCLUDED.predicted_new_racks_60d,
                predicted_new_racks_90d = EXCLUDED.predicted_new_racks_90d,
                expected_server_growth_30d = EXCLUDED.expected_server_growth_30d,
                expected_server_growth_60d = EXCLUDED.expected_server_growth_60d,
                expected_server_growth_90d = EXCLUDED.expected_server_growth_90d,
                power_headroom_pct = EXCLUDED.power_headroom_pct,
                details = EXCLUDED.details,
                updated_at = now()
            """
        ),
        [{"run_id": run_id, **_with_details(r)} for r in rows],
    )
    return len(rows)


def write_recommendations(conn: Connection, run_id: str, recs: list[dict]) -> int:
    touched = {(r["scope"], r["scope_id"]) for r in recs}
    for scope, scope_id in touched:
        conn.execute(
            text(
                "DELETE FROM ml_recommendations "
                "WHERE scope=:s AND scope_id=:sid AND status='open'"
            ),
            {"s": scope, "sid": scope_id},
        )
    if not recs:
        return 0
    conn.execute(
        text(
            """
            INSERT INTO ml_recommendations
                (run_id, scope, scope_id, category, severity, message,
                 confidence, predicted_date, details, status)
            VALUES
                (:run_id, :scope, :scope_id, :category, :severity, :message,
                 :confidence, :predicted_date, CAST(:details AS JSONB), 'open')
            """
        ),
        [{"run_id": run_id, **_with_details(r)} for r in recs],
    )
    return len(recs)


def write_model_runs(conn: Connection, run_id: str, runs: list[dict]) -> int:
    if not runs:
        return 0
    conn.execute(
        text(
            """
            INSERT INTO ml_model_runs
                (run_id, scope, scope_id, target, model_name, model_version,
                 n_points, mae, mape, accepted, notes)
            VALUES
                (:run_id, :scope, :scope_id, :target, :model_name, :model_version,
                 :n_points, :mae, :mape, :accepted, :notes)
            """
        ),
        [{"run_id": run_id, **r} for r in runs],
    )
    return len(runs)


def last_run_at(conn: Connection):
    """Most recent training timestamp, or None if never run."""
    return conn.execute(text("SELECT max(trained_at) FROM ml_model_runs")).scalar()


def _with_details(row: dict) -> dict:
    """Serialize the `details` dict to a JSON string for the JSONB cast."""
    r = dict(row)
    r["details"] = json.dumps(r.get("details") or {})
    return r
