import pandas as pd

from app.models.forecasters import TrendForecaster
from app.models.selection import select_forecaster


def _daily(values):
    idx = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype="float64")


def test_trend_extrapolates_and_is_nonnegative():
    s = _daily([float(i) for i in range(1, 11)])  # 1..10
    pts = TrendForecaster().fit(s).predict(5)
    assert len(pts) == 5
    # next-day value should be ~11 following the slope
    assert 10.0 < pts[0].value < 12.0
    # interval ordering + non-negative lower bound
    for p in pts:
        assert p.lower <= p.value <= p.upper
        assert p.lower >= 0.0


def test_select_falls_back_on_short_series():
    s = _daily([5.0, 6.0, 7.0])  # too short for HW / XGBoost
    sel = select_forecaster(s, horizon_days=10)
    assert sel is not None
    assert sel.name == "trend"
    assert len(sel.points) == 10


def test_select_returns_none_when_insufficient():
    assert select_forecaster(_daily([42.0]), horizon_days=5) is None


def test_select_picks_a_model_on_longer_series():
    # linear ramp with mild noise over 60 days
    vals = [i * 0.5 + (1 if i % 2 else -1) * 0.2 for i in range(60)]
    sel = select_forecaster(_daily(vals), horizon_days=30)
    assert sel is not None
    assert sel.n_points == 60
    assert len(sel.points) == 30
    assert sel.mae is not None  # backtest ran
