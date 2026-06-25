"""Concrete forecasters, cheapest/most-robust first.

  - TrendForecaster      : robust linear trend, works on ~2+ points (always the
                           graceful-degradation fallback).
  - HoltWintersForecaster: exponential smoothing w/ trend (+weekly seasonality
                           when enough history).
  - XGBoostLagForecaster : gradient-boosted lag/rolling features (long series).

Every forecaster returns a daily curve (day 1..horizon) with a prediction
interval that widens with horizon. Lower bounds are clipped at 0 — all targets
(%, kW, GB, U, counts) are non-negative.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.features.engineering import build_lag_frame
from app.models.base import ForecastPoint, Forecaster

_Z = 1.96  # ~95% interval


def _future_index(last_date: pd.Timestamp, horizon: int) -> pd.DatetimeIndex:
    return pd.date_range(last_date + pd.Timedelta(days=1), periods=horizon, freq="D")


def _band(resid_std: float, n: int, step: int) -> float:
    """Half-width of the interval, growing with the forecast step."""
    return _Z * max(resid_std, 1e-9) * float(np.sqrt(1.0 + step / max(n, 1)))


class TrendForecaster(Forecaster):
    name = "trend"
    min_points = 2

    def fit(self, series: pd.Series) -> "TrendForecaster":
        s = series.dropna().astype("float64")
        self._last_date = s.index.max()
        self._n = len(s)
        x = np.arange(self._n, dtype="float64")
        y = s.to_numpy()
        # Robust-ish: ordinary least squares (cheap, stable on tiny samples).
        self._slope, self._intercept = np.polyfit(x, y, 1)
        fitted = self._intercept + self._slope * x
        self._resid_std = float(np.std(y - fitted)) if self._n > 2 else float(np.std(y) or 1.0)
        return self

    def predict(self, horizon_days: int) -> list[ForecastPoint]:
        idx = _future_index(self._last_date, horizon_days)
        out = []
        for step, dt in enumerate(idx, start=1):
            x = self._n - 1 + step
            val = self._intercept + self._slope * x
            band = _band(self._resid_std, self._n, step)
            out.append(ForecastPoint(dt.to_pydatetime(), max(val, 0.0),
                                     max(val - band, 0.0), max(val + band, 0.0)))
        return out


class HoltWintersForecaster(Forecaster):
    name = "holt_winters"
    min_points = 10

    def fit(self, series: pd.Series) -> "HoltWintersForecaster":
        from statsmodels.tsa.holtwinters import ExponentialSmoothing

        s = series.dropna().astype("float64")
        self._last_date = s.index.max()
        self._n = len(s)
        seasonal = "add" if self._n >= 14 else None
        periods = 7 if seasonal else None
        self._model = ExponentialSmoothing(
            s.to_numpy(), trend="add", seasonal=seasonal,
            seasonal_periods=periods, initialization_method="estimated",
        ).fit()
        resid = s.to_numpy() - self._model.fittedvalues
        self._resid_std = float(np.std(resid)) or 1.0
        return self

    def predict(self, horizon_days: int) -> list[ForecastPoint]:
        fc = np.asarray(self._model.forecast(horizon_days), dtype="float64")
        idx = _future_index(self._last_date, horizon_days)
        out = []
        for step, (dt, val) in enumerate(zip(idx, fc), start=1):
            band = _band(self._resid_std, self._n, step)
            out.append(ForecastPoint(dt.to_pydatetime(), max(float(val), 0.0),
                                     max(float(val) - band, 0.0), max(float(val) + band, 0.0)))
        return out


class XGBoostLagForecaster(Forecaster):
    name = "xgboost"
    min_points = 21

    def __init__(self, lags: int = 7):
        self.lags = lags

    def fit(self, series: pd.Series) -> "XGBoostLagForecaster":
        from xgboost import XGBRegressor

        s = series.dropna().astype("float64")
        self._series = s
        self._last_date = s.index.max()
        self._n = len(s)
        frame = build_lag_frame(s, lags=self.lags)
        self._cols = [c for c in frame.columns if c != "y"]
        X, y = frame[self._cols].to_numpy(), frame["y"].to_numpy()
        self._model = XGBRegressor(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.9, colsample_bytree=0.9, reg_lambda=1.0,
            objective="reg:squarederror", n_jobs=2, verbosity=0,
        )
        self._model.fit(X, y)
        self._resid_std = float(np.std(y - self._model.predict(X))) or 1.0
        self._t_last = float(frame["t"].iloc[-1])
        return self

    def _features(self, hist: list[float], step: int) -> np.ndarray:
        row = {f"lag_{k}": hist[-k] for k in range(1, self.lags + 1)}
        recent = np.asarray(hist, dtype="float64")
        row["roll_mean_3"] = float(recent[-3:].mean())
        row["roll_mean_7"] = float(recent[-7:].mean())
        std7 = float(pd.Series(recent[-7:]).std())
        row["roll_std_7"] = 0.0 if np.isnan(std7) else std7
        row["t"] = self._t_last + step
        return np.array([[row[c] for c in self._cols]], dtype="float64")

    def predict(self, horizon_days: int) -> list[ForecastPoint]:
        hist = list(self._series.to_numpy())
        idx = _future_index(self._last_date, horizon_days)
        out = []
        for step, dt in enumerate(idx, start=1):
            val = float(self._model.predict(self._features(hist, step))[0])
            hist.append(val)
            band = _band(self._resid_std, self._n, step)
            out.append(ForecastPoint(dt.to_pydatetime(), max(val, 0.0),
                                     max(val - band, 0.0), max(val + band, 0.0)))
        return out


#: Candidate models, ordered most-capable → fallback.
CANDIDATES: list[type[Forecaster]] = [
    XGBoostLagForecaster,
    HoltWintersForecaster,
    TrendForecaster,
]
