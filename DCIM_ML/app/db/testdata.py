"""Synthetic time-series test data mirroring the aggregator DB schema.

Each generator produces a pandas Series with a daily DatetimeIndex — the same
shape that source_queries.py returns.  Schema reference:
  - metrics / metrics_rollup_1d  (001_init.sql, 014_metric_rollups.sql)
  - energy_metrics / energy_rollup_1d  (007_energy_metrics.sql)
  - devices.created_at growth curve  (001_init.sql)
  - dc_inventory_devices snapshot  (005_inventory.sql)

Used by:
  - tests/ (no live DB needed)
  - pipeline startup when DB has < MIN_POINTS of history (dev/staging)
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd


# ── RNG ───────────────────────────────────────────────────────────────────────

def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def _daily_index(n_days: int, end: date | None = None) -> pd.DatetimeIndex:
    end = end or date.today()
    start = end - timedelta(days=n_days - 1)
    return pd.date_range(start=str(start), end=str(end), freq="D")


# ── Per-target generators ─────────────────────────────────────────────────────

def generate_cpu_series(n_days: int = 365, seed: int = 42) -> pd.Series:
    """Daily avg CPU utilization (%).

    Simulates a datacenter growing into capacity:
      - Slow upward trend 35 → 65 %
      - ±5 % weekly business-day cycle
      - One mid-window stress spike (+12 % for 7 days)
      - Gaussian noise σ=2.5 %

    Mirrors metric_names ['system.cpu_utilization_percent', 'server.cpu_percent']
    averaged daily from metrics_rollup_1d.
    """
    rng = _rng(seed)
    idx = _daily_index(n_days)
    t = np.arange(n_days, dtype="float64")

    trend = 35.0 + 30.0 * (t / max(n_days - 1, 1))
    weekly = 5.0 * np.sin(2 * math.pi * t / 7)
    noise = rng.normal(0.0, 2.5, n_days)
    spike = np.zeros(n_days)
    mid = n_days // 2
    spike[mid : mid + 7] = 12.0

    return pd.Series(np.clip(trend + weekly + noise + spike, 5.0, 99.0),
                     index=idx, dtype="float64")


def generate_memory_series(n_days: int = 365, seed: int = 43) -> pd.Series:
    """Daily avg memory utilization (%).

    Slightly ahead of CPU: 45 → 75 %.
    Mirrors metric_name 'system.memory_utilization_percent'.
    """
    rng = _rng(seed)
    idx = _daily_index(n_days)
    t = np.arange(n_days, dtype="float64")

    trend = 45.0 + 30.0 * (t / max(n_days - 1, 1))
    weekly = 3.0 * np.sin(2 * math.pi * t / 7)
    noise = rng.normal(0.0, 1.5, n_days)

    return pd.Series(np.clip(trend + weekly + noise, 5.0, 99.0),
                     index=idx, dtype="float64")


def generate_temperature_series(n_days: int = 365, seed: int = 44) -> pd.Series:
    """Daily avg inlet temperature (°C).

    Slow rise 18 → 27 °C as the DC fills; annual seasonal summer peak.
    Mirrors metric_name 'system.temperature_celsius' from device_thresholds
    (HighTemperature rule) and metrics_rollup_1d.
    """
    rng = _rng(seed)
    idx = _daily_index(n_days)
    t = np.arange(n_days, dtype="float64")

    trend = 18.0 + 9.0 * (t / max(n_days - 1, 1))
    seasonal = 3.0 * np.sin(2 * math.pi * t / 365.0 - math.pi / 2)
    noise = rng.normal(0.0, 0.6, n_days)

    return pd.Series(np.clip(trend + seasonal + noise, 14.0, 40.0),
                     index=idx, dtype="float64")


def generate_storage_series(n_days: int = 365, seed: int = 45) -> pd.Series:
    """Daily avg disk usage (%).

    Near-monotonic climb 30 → 82 %.
    Mirrors metric_names ['system.disk_usage_percent', 'server.disk_used_percent'].
    """
    rng = _rng(seed)
    idx = _daily_index(n_days)
    t = np.arange(n_days, dtype="float64")

    trend = 30.0 + 52.0 * (t / max(n_days - 1, 1))
    noise = rng.normal(0.0, 0.4, n_days)

    return pd.Series(np.clip(trend + noise, 5.0, 99.5),
                     index=idx, dtype="float64")


def generate_power_series(n_days: int = 365, seed: int = 46) -> pd.Series:
    """Daily total facility active power (kW).

    Continuous growth 120 → 210 kW + step jumps when new racks come online
    (every ~90 days +15 kW).
    Mirrors energy_rollup_1d metric_name 'energy.active_power_kw'.
    """
    rng = _rng(seed)
    idx = _daily_index(n_days)
    t = np.arange(n_days, dtype="float64")

    base = 120.0 + 90.0 * (t / max(n_days - 1, 1))
    steps = np.zeros(n_days)
    for step_day in range(90, n_days, 90):
        steps[step_day:] += 15.0
    noise = rng.normal(0.0, 3.0, n_days)

    return pd.Series(np.clip(base + steps + noise, 80.0, 500.0),
                     index=idx, dtype="float64")


def generate_server_count_series(n_days: int = 365, seed: int = 47) -> pd.Series:
    """Cumulative server count — step-function growth.

    Starts at 50, adds 2–4 servers every 30 days.
    Mirrors fetch_server_count_series which reads devices.created_at
    grouped into daily cumulative counts.
    """
    rng = _rng(seed)
    idx = _daily_index(n_days)
    counts = np.empty(n_days, dtype="float64")
    current = 50.0
    for i in range(n_days):
        if i > 0 and i % 30 == 0:
            current += float(rng.integers(2, 5))
        counts[i] = current
    return pd.Series(counts, index=idx, dtype="float64")


# ── Aggregate accessor ────────────────────────────────────────────────────────

def generate_all_series(n_days: int = 365) -> dict[str, pd.Series]:
    """All six prediction targets as {target_name: daily Series}."""
    return {
        "cpu":          generate_cpu_series(n_days),
        "memory":       generate_memory_series(n_days),
        "temperature":  generate_temperature_series(n_days),
        "storage":      generate_storage_series(n_days),
        "power":        generate_power_series(n_days),
        "server_count": generate_server_count_series(n_days),
    }


# ── Capacity snapshot ─────────────────────────────────────────────────────────

def generate_capacity_snapshot(
    n_devices: int = 65,
    n_racks: int = 4,
    used_u_per_rack: float = 30.0,
    seed: int = 42,
) -> dict:
    """Synthetic capacity snapshot matching fetch_capacity_snapshot() shape.

    Returns {"totals": {...}, "racks": [...]}, mirroring dc_inventory_devices
    columns: rack_unit_height, storage_gb, power_capacity_w, status, etc.
    """
    rng = _rng(seed)
    avg_u = 2.0          # rack_unit_height average
    avg_storage = 200.0  # storage_gb per device
    avg_power = 500.0    # power_capacity_w per device

    racks = [
        {
            "datacenter_id": "dc1",
            "rack": f"R{i + 1:02d}",
            "used_u": round(used_u_per_rack + float(rng.integers(-3, 4)), 1),
            "devices": int(used_u_per_rack / avg_u),
        }
        for i in range(n_racks)
    ]

    total_storage_gb = n_devices * avg_storage * 1.5   # 33 % head-room
    used_storage_gb  = n_devices * avg_storage * 0.82

    totals = {
        "total_devices":          n_devices,
        "active_devices":         n_devices - 3,
        "total_storage_gb":       round(total_storage_gb, 1),
        "used_storage_gb":        round(used_storage_gb, 1),
        "total_power_capacity_w": n_racks * 60_000,  # 60 kW/rack → 240 kW total
        "current_power_w":        n_devices * avg_power * 0.75,
        "avg_u_per_device":       avg_u,
        "avg_storage_per_device": avg_storage,
        "avg_power_per_device":   avg_power,
    }
    return {"totals": totals, "racks": racks}
