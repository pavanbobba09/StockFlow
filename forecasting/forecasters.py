"""
Baseline forecasters for per-store-item demand prediction.

Both are intentionally simple — they are the comparison baseline every
future improvement must beat on the backtest scorecard.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import date, timedelta
from typing import List
import numpy as np


class BaseForecaster(ABC):
    @abstractmethod
    def fit(self, dates: List[date], quantities: List[int]) -> None:
        """Train on historical demand (dates must be sorted ascending)."""

    @abstractmethod
    def predict(self, horizon: int) -> List[float]:
        """Return predicted demand for the next `horizon` days."""

    def predict_total(self, horizon: int) -> float:
        return sum(self.predict(horizon))


class MovingAverageForecaster(BaseForecaster):
    """
    Simple moving average over the last `window` days.
    Predicts the same average for every day in the horizon.
    Captures level but ignores weekday patterns.
    """

    def __init__(self, window: int = 14):
        self.window = window
        self._mean: float = 0.0

    def fit(self, dates: List[date], quantities: List[int]) -> None:
        if not quantities:
            self._mean = 0.0
            return
        recent = quantities[-self.window:]
        self._mean = float(np.mean(recent))

    def predict(self, horizon: int) -> List[float]:
        return [self._mean] * horizon

    def __repr__(self):
        return f"MovingAverageForecaster(window={self.window})"


class SeasonalNaiveForecaster(BaseForecaster):
    """
    Seasonal naive: predict demand for weekday d = mean of the last `k`
    occurrences of that weekday in history.

    Captures weekly seasonality (weekend spikes, quiet Mondays, etc.)
    without any complex fitting.
    """

    def __init__(self, k: int = 4):
        self.k = k
        self._by_weekday: dict[int, float] = {}
        self._last_date: date | None = None

    def fit(self, dates: List[date], quantities: List[int]) -> None:
        if not dates:
            return
        self._last_date = dates[-1]
        by_wd: dict[int, List[int]] = defaultdict(list)
        for d, q in zip(dates, quantities):
            by_wd[d.weekday()].append(q)
        self._by_weekday = {
            wd: float(np.mean(vals[-self.k:]))
            for wd, vals in by_wd.items()
        }

    def predict(self, horizon: int) -> List[float]:
        if self._last_date is None:
            return [0.0] * horizon
        preds = []
        for i in range(1, horizon + 1):
            future_date = self._last_date + timedelta(days=i)
            wd = future_date.weekday()
            preds.append(self._by_weekday.get(wd, 0.0))
        return preds

    def __repr__(self):
        return f"SeasonalNaiveForecaster(k={self.k})"
