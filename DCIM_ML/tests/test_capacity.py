from datetime import date

import pandas as pd

from app.engines.capacity import compute_capacity
from app.models.base import ForecastPoint


def _server_curve(today, current, per_day, horizon=90):
    idx = pd.date_range(pd.Timestamp(today) + pd.Timedelta(days=1), periods=horizon, freq="D")
    pts = []
    for step, dt in enumerate(idx, start=1):
        v = current + per_day * step
        pts.append(ForecastPoint(dt.to_pydatetime(), v, v, v))
    return pts


def _snapshot():
    return {
        "totals": {
            "total_devices": 12, "active_devices": 10,
            "total_storage_gb": 1000.0, "used_storage_gb": 800.0,
            "total_power_capacity_w": 6000, "current_power_w": 1000.0,
            "avg_u_per_device": 2.0, "avg_storage_per_device": 100.0,
            "avg_power_per_device": 500.0,
        },
        "racks": [{"datacenter_id": "dc1", "rack": "R1", "used_u": 40.0, "devices": 10}],
    }


def test_rack_and_storage_exhaustion():
    today = date(2026, 1, 1)
    server_points = _server_curve(today, current=10.0, per_day=0.5)
    res = compute_capacity(
        _snapshot(), server_points, current_servers=10.0, power_points=None,
        rack_u_default=42, rack_power_capacity_w=2000, horizons=[30, 60, 90], today=today,
    )
    s = res.summary
    # used_u=40, avg_u=2 -> +1 server crosses 42U capacity (delta 1 at day 2)
    assert s["rack_exhaustion_date"] == date(2026, 1, 3)
    assert s["days_until_rack_full"] == 2
    # storage: used 800, avg 100/server -> +2 servers hits 1000 (delta 2 at day 4)
    assert s["storage_exhaustion_date"] == date(2026, 1, 5)
    # at 30d: delta=15 servers -> +30U -> 70U used, 28U over -> ceil(28/42)=1 rack
    assert s["predicted_new_racks_30d"] == 1
    assert s["expected_server_growth_30d"] == 15
    assert "rack_units" in res.derived_forecasts
    assert "storage" in res.derived_forecasts


def test_no_inventory_is_graceful():
    today = date(2026, 1, 1)
    empty = {"totals": {}, "racks": []}
    res = compute_capacity(
        empty, _server_curve(today, 0.0, 1.0), current_servers=0.0, power_points=None,
        rack_u_default=42, rack_power_capacity_w=2000, horizons=[30, 60, 90], today=today,
    )
    # No racks/capacity -> no exhaustion, but must not crash.
    assert res.summary["rack_exhaustion_date"] is None
    assert res.summary["days_until_rack_full"] is None
