"""Backtest the candidate forecasters and pick a champion per series.

Graceful degradation: on shallow data the heavier models aren't viable and we
fall back to the linear TrendForecaster, which only needs 2 points.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.logging_conf import get_logger
from app.models.base import ForecastPoint, Forecaster
from app.models.forecasters import CANDIDATES, TrendForecaster

log = get_logger("dcim_ml.selection")


@dataclass
class Selection:
    model: Forecaster        # fitted on the full series
    name: str
    points: list[ForecastPoint]
    n_points: int
    mae: float | None
    mape: float | None


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float, float]:
    err = y_true - y_pred
    mae = float(np.mean(np.abs(err)))
    denom = np.where(np.abs(y_true) < 1e-9, 1e-9, np.abs(y_true))
    mape = float(np.mean(np.abs(err / denom)) * 100.0)
    return mae, mape


def _backtest(cls: type[Forecaster], series: pd.Series, test_size: int):
    train, test = series.iloc[:-test_size], series.iloc[-test_size:]
    inst = cls()
    if not inst.viable(train):
        return None
    inst.fit(train)
    preds = np.array([p.value for p in inst.predict(test_size)], dtype="float64")
    return _metrics(test.to_numpy(), preds)


def select_forecaster(series: pd.Series, horizon_days: int) -> Selection | None:
    s = series.dropna().astype("float64")
    n = len(s)
    if n < 2:
        return None

    test_size = min(7, max(1, n // 4))
    scored: list[tuple[type[Forecaster], float, float]] = []
    for cls in CANDIDATES:
        if not cls().viable(s):
            continue
        if n - test_size < cls.min_points:
            continue
        try:
            res = _backtest(cls, s, test_size)
        except Exception as exc:  # noqa: BLE001
            log.warning("backtest_failed", model=cls.name, error=str(exc))
            res = None
        if res is not None:
            scored.append((cls, res[0], res[1]))

    if scored:
        champion_cls, mae, mape = min(scored, key=lambda t: t[1])
    else:
        champion_cls, mae, mape = TrendForecaster, None, None

    try:
        model = champion_cls().fit(s)
    except Exception as exc:  # noqa: BLE001
        log.warning("champion_fit_failed", model=champion_cls.name, error=str(exc))
        model = TrendForecaster().fit(s)
        mae = mape = None

    return Selection(
        model=model, name=model.name, points=model.predict(horizon_days),
        n_points=n, mae=mae, mape=mape,
    )
