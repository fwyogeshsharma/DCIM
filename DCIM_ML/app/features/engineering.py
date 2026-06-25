"""Feature engineering over daily series (spec section 7).

Two layers:
  - `summarize_series`  : scalar features describing one utilization/growth series
    (rolling means/std, EMA, growth rates, peaks) — surfaced in forecast details.
  - `build_lag_frame`   : supervised lag/rolling matrix for the XGBoost forecaster.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def summarize_series(s: pd.Series) -> dict:
    """Scalar utilization/growth/trend features for a single daily series."""
    s = s.dropna().astype("float64")
    if s.empty:
        return {}

    def _avg(days: int) -> float | None:
        tail = s.tail(days)
        return float(tail.mean()) if len(tail) else None

    def _growth(days: int) -> float | None:
        if len(s) <= days:
            return None
        prev, cur = float(s.iloc[-days - 1]), float(s.iloc[-1])
        return cur - prev

    out = {
        "n_points": int(len(s)),
        "last_value": float(s.iloc[-1]),
        "avg_1d": _avg(1),
        "avg_7d": _avg(7),
        "avg_30d": _avg(30),
        "peak": float(s.max()),
        "trough": float(s.min()),
        "growth_daily": _growth(1),
        "growth_weekly": _growth(7),
        "growth_monthly": _growth(30),
        "rolling_mean_7d": float(s.rolling(7, min_periods=1).mean().iloc[-1]),
        "rolling_std_7d": float(s.rolling(7, min_periods=1).std().fillna(0.0).iloc[-1]),
        "ema_7d": float(s.ewm(span=7, adjust=False).mean().iloc[-1]),
    }
    # Average per-day slope over the whole window (robust to length).
    if len(s) >= 2:
        x = np.arange(len(s), dtype="float64")
        slope = np.polyfit(x, s.to_numpy(), 1)[0]
        out["slope_per_day"] = float(slope)
    return out


def build_lag_frame(s: pd.Series, lags: int = 7) -> pd.DataFrame:
    """Supervised frame: target `y` with lagged + rolling features.

    Index is the target date; columns are lag_1..lag_n plus rolling mean/std and
    a linear time index. Rows with insufficient history are dropped.
    """
    s = s.dropna().astype("float64")
    df = pd.DataFrame({"y": s})
    for k in range(1, lags + 1):
        df[f"lag_{k}"] = s.shift(k)
    df["roll_mean_3"] = s.shift(1).rolling(3, min_periods=1).mean()
    df["roll_mean_7"] = s.shift(1).rolling(7, min_periods=1).mean()
    df["roll_std_7"] = s.shift(1).rolling(7, min_periods=1).std()
    df["t"] = np.arange(len(df), dtype="float64")
    return df.dropna()
