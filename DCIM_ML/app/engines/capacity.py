"""Capacity calculation engine (spec section 9).

Turns the server-growth forecast + the current physical snapshot into rack /
storage / power projections, exhaustion dates, and racks-to-buy numbers. Rack
and storage usage have no real time-series history yet, so they are *derived*
from the (real) server-growth forecast using per-device averages.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.models.base import ForecastPoint


@dataclass
class CapacityResult:
    summary: dict
    derived_forecasts: dict[str, list[ForecastPoint]] = field(default_factory=dict)


def _first_crossing(points: list[ForecastPoint], capacity: float) -> ForecastPoint | None:
    if capacity <= 0:
        return None
    for p in points:
        if p.value >= capacity:
            return p
    return None


def _at_horizon(points: list[ForecastPoint], horizon: int) -> ForecastPoint | None:
    if not points:
        return None
    return points[min(horizon, len(points)) - 1]


def compute_capacity(
    snapshot: dict,
    server_points: list[ForecastPoint],
    current_servers: float,
    power_points: list[ForecastPoint] | None,
    *,
    rack_u_default: int,
    rack_power_capacity_w: int,
    horizons: list[int],
    today: date,
) -> CapacityResult:
    totals = snapshot.get("totals") or {}
    racks = snapshot.get("racks") or []

    avg_u = float(totals.get("avg_u_per_device") or 1.0)
    avg_storage = float(totals.get("avg_storage_per_device") or 0.0)
    avg_power = float(totals.get("avg_power_per_device") or 0.0)

    n_racks = len([r for r in racks if r.get("rack") not in (None, "", "unknown")]) or len(racks)
    total_u = n_racks * rack_u_default
    used_u = float(sum(float(r.get("used_u") or 0.0) for r in racks))

    total_storage = float(totals.get("total_storage_gb") or 0.0)
    used_storage = float(totals.get("used_storage_gb") or 0.0)

    total_power_avail = float(n_racks * rack_power_capacity_w) if n_racks else 0.0
    current_power_w = float(totals.get("current_power_w") or 0.0)

    # ── Build derived per-day curves from the server-growth forecast ──────────
    rack_curve: list[ForecastPoint] = []
    storage_curve: list[ForecastPoint] = []
    power_curve: list[ForecastPoint] = []
    for p in server_points:
        delta = p.value - current_servers
        d_lo = p.lower - current_servers
        d_hi = p.upper - current_servers
        rack_curve.append(ForecastPoint(
            p.forecast_for, used_u + delta * avg_u,
            max(used_u + d_lo * avg_u, 0.0), used_u + d_hi * avg_u))
        storage_curve.append(ForecastPoint(
            p.forecast_for, used_storage + delta * avg_storage,
            max(used_storage + d_lo * avg_storage, 0.0), used_storage + d_hi * avg_storage))
        power_curve.append(ForecastPoint(
            p.forecast_for, current_power_w + delta * avg_power,
            max(current_power_w + d_lo * avg_power, 0.0), current_power_w + d_hi * avg_power))

    # ── Exhaustion dates ──────────────────────────────────────────────────────
    rack_hit = _first_crossing(rack_curve, total_u)
    storage_hit = _first_crossing(storage_curve, total_storage)

    def _days_until(p: ForecastPoint | None) -> int | None:
        if p is None:
            return None
        return max((p.forecast_for.date() - today).days, 0)

    def _as_date(p: ForecastPoint | None) -> date | None:
        return p.forecast_for.date() if p else None

    # ── Per-horizon numbers ───────────────────────────────────────────────────
    new_racks: dict[int, int | None] = {}
    server_growth: dict[int, int | None] = {}
    for h in horizons:
        rp = _at_horizon(rack_curve, h)
        if rp is not None and total_u > 0:
            over = rp.value - total_u
            new_racks[h] = int(math.ceil(over / rack_u_default)) if over > 0 else 0
        else:
            new_racks[h] = None
        sp = _at_horizon(server_points, h)
        server_growth[h] = int(round(sp.value - current_servers)) if sp is not None else None

    # ── Power headroom at the first horizon ───────────────────────────────────
    headroom = None
    if total_power_avail > 0:
        ref = power_points if power_points else power_curve
        php = _at_horizon(ref, horizons[0]) if ref else None
        # power_points is kW (facility meters); power_curve is W (device PSU sum).
        projected_w = (php.value * 1000.0 if power_points else php.value) if php else current_power_w
        headroom = round((total_power_avail - projected_w) / total_power_avail * 100.0, 1)

    summary = {
        "days_until_rack_full": _days_until(rack_hit),
        "rack_exhaustion_date": _as_date(rack_hit),
        "days_until_storage_full": _days_until(storage_hit),
        "storage_exhaustion_date": _as_date(storage_hit),
        "predicted_new_racks_30d": new_racks.get(30),
        "predicted_new_racks_60d": new_racks.get(60),
        "predicted_new_racks_90d": new_racks.get(90),
        "expected_server_growth_30d": server_growth.get(30),
        "expected_server_growth_60d": server_growth.get(60),
        "expected_server_growth_90d": server_growth.get(90),
        "power_headroom_pct": headroom,
        "details": {
            "n_racks": n_racks,
            "rack_u_capacity": total_u,
            "rack_u_used": round(used_u, 1),
            "total_storage_gb": round(total_storage, 1),
            "used_storage_gb": round(used_storage, 1),
            "current_servers": int(round(current_servers)),
            "avg_u_per_device": round(avg_u, 2),
            "avg_storage_per_device_gb": round(avg_storage, 1),
        },
    }
    derived = {"rack_units": rack_curve, "storage": storage_curve}
    return CapacityResult(summary=summary, derived_forecasts=derived)
