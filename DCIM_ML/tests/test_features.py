import numpy as np
import pandas as pd

from app.features.engineering import build_lag_frame, summarize_series


def _daily(values):
    idx = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return pd.Series(values, index=idx, dtype="float64")


def test_summarize_empty():
    assert summarize_series(pd.Series(dtype="float64")) == {}


def test_summarize_basic_trend():
    s = _daily(list(range(1, 31)))  # 1..30, slope = 1/day
    feats = summarize_series(s)
    assert feats["n_points"] == 30
    assert feats["last_value"] == 30.0
    assert feats["peak"] == 30.0
    assert abs(feats["slope_per_day"] - 1.0) < 1e-6
    assert feats["growth_weekly"] == 7.0


def test_build_lag_frame_shapes():
    s = _daily(list(range(1, 21)))
    frame = build_lag_frame(s, lags=7)
    assert "y" in frame.columns
    assert {f"lag_{k}" for k in range(1, 8)}.issubset(frame.columns)
    # 20 points, 7 lags -> at most 13 usable rows
    assert len(frame) == 13
    assert not frame.isna().any().any()
