"""Read-only queries against the aggregator's source tables.

Everything here is SELECT-only. Series are returned as pandas Series indexed by
a daily DatetimeIndex so the forecasters can treat every target uniformly.

Schema references (see DCIM_Aggregator/src/database/migrations):
  - metrics / metrics_rollup_1d        (001_init.sql, 014_metric_rollups.sql)
  - energy_metrics / energy_rollup_1d  (007_energy_metrics.sql, 014_metric_rollups.sql)
  - devices                            (001_init.sql, 003/011/013)
  - dc_inventory_devices               (005_inventory.sql)
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.logging_conf import get_logger

log = get_logger("dcim_ml.queries")

# Metric-name groups, mirroring the CPU/RAM cascade used by the aggregator's
# inventory route (DCIM_Aggregator/src/api/routes/inventory.ts). Primary names
# first; the query averages whichever names are present.
METRIC_GROUPS: dict[str, list[str]] = {
    "cpu": ["system.cpu_utilization_percent", "server.cpu_percent"],
    "memory": ["system.memory_utilization_percent"],
    "storage": ["system.disk_usage_percent", "server.disk_used_percent"],
    "temperature": ["system.temperature_celsius"],
}

# Target → display unit, carried through to ml_forecasts.unit.
# Note: "storage" from fetch_metric_series is a utilisation percent
# (system.disk_usage_percent); the GB-valued derived curve from the capacity
# engine is stored under the "storage" key but labelled by the capacity engine
# itself. "rack_units" is only ever derived.
TARGET_UNITS: dict[str, str] = {
    "cpu":          "%",
    "memory":       "%",
    "temperature":  "°C",
    "storage":      "%",
    "power":        "kW",
    "rack_units":   "U",
    "server_count": "servers",
}


def _dc_clause(alias: str, dc: str | None) -> str:
    """Optional datacenter filter on a `devices`-shaped alias."""
    return f" AND {alias}.datacenter = :dc" if dc else ""


def list_datacenters(conn: Connection) -> list[str]:
    """Distinct monitored datacenters (the unit metrics/server-counts key on)."""
    rows = conn.execute(
        text(
            "SELECT DISTINCT datacenter FROM devices "
            "WHERE datacenter IS NOT NULL AND datacenter <> '' ORDER BY 1"
        )
    ).fetchall()
    return [r[0] for r in rows]


def fetch_metric_series(
    conn: Connection, target: str, dc: str | None = None, lookback_days: int = 365
) -> pd.Series:
    """Daily average of a utilization metric across (optionally one) datacenter.

    Prefers the 1-day rollup; falls back to bucketing raw `metrics` when the
    rollup has too few points (typical on a young / shallow deployment).
    """
    names = METRIC_GROUPS.get(target, [])
    if not names:
        return pd.Series(dtype="float64")

    rollup = conn.execute(
        text(
            f"""
            SELECT r.bucket::date AS bucket, avg(r.avg_value) AS value
            FROM metrics_rollup_1d r
            JOIN devices d ON d.id = r.device_id
            WHERE r.metric_name = ANY(:names)
              AND r.bucket >= now() - make_interval(days => :lb)
              {_dc_clause('d', dc)}
            GROUP BY 1 ORDER BY 1
            """
        ),
        {"names": names, "lb": lookback_days, **({"dc": dc} if dc else {})},
    ).fetchall()
    s = _rows_to_series(rollup)
    if len(s) >= 3:
        return s

    # Fallback: raw metrics bucketed to days (raw retention is ~30 days).
    raw = conn.execute(
        text(
            f"""
            SELECT time_bucket('1 day', m.ts)::date AS bucket, avg(m.value) AS value
            FROM metrics m
            JOIN devices d ON d.id = m.device_id
            WHERE m.metric_name = ANY(:names)
              AND m.ts >= now() - make_interval(days => :lb)
              {_dc_clause('d', dc)}
            GROUP BY 1 ORDER BY 1
            """
        ),
        {"names": names, "lb": lookback_days, **({"dc": dc} if dc else {})},
    ).fetchall()
    return _rows_to_series(raw)


def fetch_power_series(
    conn: Connection, dc: str | None = None, lookback_days: int = 365
) -> pd.Series:
    """Daily total facility active power (kW).

    Uses the panel scalar reading (empty circuit) per device to avoid double
    counting per-circuit rows, then sums devices — mirroring energy.ts.
    """
    rollup = conn.execute(
        text(
            f"""
            SELECT bucket::date AS bucket, sum(dev_avg) AS value FROM (
                SELECT r.bucket, r.device_id, avg(r.avg_value) AS dev_avg
                FROM energy_rollup_1d r
                JOIN devices d ON d.id = r.device_id
                WHERE r.metric_name = 'energy.active_power_kw'
                  AND coalesce(r.circuit, '') = ''
                  AND r.bucket >= now() - make_interval(days => :lb)
                  {_dc_clause('d', dc)}
                GROUP BY r.bucket, r.device_id
            ) s GROUP BY 1 ORDER BY 1
            """
        ),
        {"lb": lookback_days, **({"dc": dc} if dc else {})},
    ).fetchall()
    s = _rows_to_series(rollup)
    if len(s) >= 3:
        return s

    raw = conn.execute(
        text(
            f"""
            SELECT bucket::date AS bucket, sum(dev_avg) AS value FROM (
                SELECT time_bucket('1 day', e.ts) AS bucket, e.device_id, avg(e.value) AS dev_avg
                FROM energy_metrics e
                JOIN devices d ON d.id = e.device_id
                WHERE e.metric_name = 'energy.active_power_kw'
                  AND coalesce(e.circuit, '') = ''
                  AND e.ts >= now() - make_interval(days => :lb)
                  {_dc_clause('d', dc)}
                GROUP BY 1, e.device_id
            ) s GROUP BY 1 ORDER BY 1
            """
        ),
        {"lb": lookback_days, **({"dc": dc} if dc else {})},
    ).fetchall()
    return _rows_to_series(raw)


def fetch_server_count_series(conn: Connection, dc: str | None = None) -> pd.Series:
    """Cumulative count of server devices per day, from `devices.created_at`.

    This is genuine history (when devices were added) and is the primary growth
    driver for the capacity engine even when utilization history is shallow.
    """
    rows = conn.execute(
        text(
            f"""
            WITH bounds AS (
                SELECT date_trunc('day', min(created_at)) AS d0,
                       date_trunc('day', now())           AS d1
                FROM devices d
                WHERE device_type = 'server' {_dc_clause('d', dc)}
            ),
            days AS (
                SELECT generate_series(d0, d1, interval '1 day') AS day
                FROM bounds WHERE d0 IS NOT NULL
            )
            SELECT days.day::date AS bucket,
                   (SELECT count(*) FROM devices x
                      WHERE x.device_type = 'server'
                        AND x.created_at < days.day + interval '1 day'
                        {_dc_clause('x', dc)})::float AS value
            FROM days ORDER BY 1
            """
        ),
        {"dc": dc} if dc else {},
    ).fetchall()
    return _rows_to_series(rows)


def fetch_capacity_snapshot(conn: Connection, dc: str | None = None) -> dict:
    """Current physical-capacity snapshot from dc_inventory_devices.

    Returns totals, per-rack used-U, and per-in-use-device averages used by the
    capacity engine to project growth into rack/storage/power exhaustion.
    """
    inv_dc = (
        " WHERE (datacenter_name = :dc OR datacenter_id = :dc)" if dc else ""
    )
    params = {"dc": dc} if dc else {}

    totals = conn.execute(
        text(
            f"""
            SELECT
                COUNT(*)                                                       AS total_devices,
                COUNT(*) FILTER (WHERE status IN ('in_use','maintenance'))     AS active_devices,
                COALESCE(SUM(storage_gb), 0)                                   AS total_storage_gb,
                COALESCE(SUM(COALESCE(storage_used_gb, 0)), 0)                 AS used_storage_gb,
                COALESCE(SUM(power_capacity_w), 0)                             AS total_power_capacity_w,
                COALESCE(SUM(COALESCE(power_draw_w, 0)), 0)                    AS current_power_w,
                COALESCE(AVG(rack_unit_height) FILTER (WHERE status IN ('in_use','maintenance')), 1) AS avg_u_per_device,
                COALESCE(AVG(storage_gb)       FILTER (WHERE status IN ('in_use','maintenance')), 0) AS avg_storage_per_device,
                COALESCE(AVG(power_capacity_w) FILTER (WHERE status IN ('in_use','maintenance')), 0) AS avg_power_per_device
            FROM dc_inventory_devices{inv_dc}
            """
        ),
        params,
    ).mappings().first()

    racks = conn.execute(
        text(
            f"""
            SELECT COALESCE(datacenter_id, 'default') AS datacenter_id,
                   COALESCE(rack, 'unknown')          AS rack,
                   COALESCE(SUM(rack_unit_height) FILTER (WHERE status IN ('in_use','maintenance')), 0) AS used_u,
                   COUNT(*) AS devices
            FROM dc_inventory_devices{inv_dc}
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ),
        params,
    ).mappings().all()

    return {
        "totals": dict(totals) if totals else {},
        "racks": [dict(r) for r in racks],
    }


def _rows_to_series(rows) -> pd.Series:
    """Convert (bucket, value) rows into a sorted, daily, gap-filled Series."""
    if not rows:
        return pd.Series(dtype="float64")
    df = pd.DataFrame(rows, columns=["bucket", "value"])
    df["bucket"] = pd.to_datetime(df["bucket"])
    df = df.dropna(subset=["value"]).set_index("bucket").sort_index()
    s = df["value"].astype("float64")
    s = s[~s.index.duplicated(keep="last")]
    if len(s) >= 2:
        # Reindex to a continuous daily grid; interpolate small gaps.
        full = pd.date_range(s.index.min(), s.index.max(), freq="D")
        s = s.reindex(full).interpolate(limit_direction="both")
    return s
