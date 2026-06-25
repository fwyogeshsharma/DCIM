"""Forecaster interface and the shared forecast-point shape."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass
class ForecastPoint:
    forecast_for: datetime
    value: float
    lower: float
    upper: float


class Forecaster(ABC):
    """Fit on a daily Series, predict N days ahead with a prediction interval."""

    name: str = "base"
    #: Minimum number of points the model needs to be considered viable.
    min_points: int = 2

    @abstractmethod
    def fit(self, series: pd.Series) -> "Forecaster":
        ...

    @abstractmethod
    def predict(self, horizon_days: int) -> list[ForecastPoint]:
        ...

    def viable(self, series: pd.Series) -> bool:
        return series.dropna().shape[0] >= self.min_points
