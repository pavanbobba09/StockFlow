"""
Metrics module. All metrics are computed over the full metric SET — never
optimise one in isolation (see CLAUDE.md: stockout vs waste trade off).
"""

from dataclasses import dataclass, field
from typing import List
import numpy as np


# ---------------------------------------------------------------------------
# Forecast accuracy
# ---------------------------------------------------------------------------

def wape(actual: List[float], predicted: List[float]) -> float:
    """Weighted Absolute Percentage Error. Robust to zero actuals."""
    a = np.array(actual, dtype=float)
    p = np.array(predicted, dtype=float)
    total = np.sum(a)
    if total == 0:
        return 0.0
    return float(np.sum(np.abs(a - p)) / total)


def mape(actual: List[float], predicted: List[float]) -> float:
    """Mean Absolute Percentage Error. Skips zero-actual days."""
    a = np.array(actual, dtype=float)
    p = np.array(predicted, dtype=float)
    mask = a > 0
    if not mask.any():
        return 0.0
    return float(np.mean(np.abs((a[mask] - p[mask]) / a[mask])))


# ---------------------------------------------------------------------------
# Accumulator — used by the backtest engine day by day
# ---------------------------------------------------------------------------

@dataclass
class DayRecord:
    date: object
    store_id: int
    item_id: int
    demand: int
    fulfilled: int
    waste: int            # units expired
    ordered: int          # units ordered this day (0 on non-delivery days)
    forecast: float       # what the forecaster predicted for today
    inventory_eod: int    # end-of-day inventory after all operations


class ScorecardAccumulator:
    """
    Collects per-day simulation events and computes the metric scorecard.
    One accumulator per backtest run covers all store-item pairs.
    """

    def __init__(self):
        self._records: List[DayRecord] = []

    def record(self, rec: DayRecord) -> None:
        self._records.append(rec)

    def scorecard(self) -> dict:
        if not self._records:
            return {}

        total_demand    = sum(r.demand    for r in self._records)
        total_fulfilled = sum(r.fulfilled for r in self._records)
        total_waste     = sum(r.waste     for r in self._records)
        total_ordered   = sum(r.ordered   for r in self._records)

        stockout_days   = sum(1 for r in self._records if r.fulfilled < r.demand)
        total_days      = len(self._records)

        actuals    = [r.demand    for r in self._records]
        forecasts  = [r.forecast  for r in self._records]

        return {
            "stockout_rate":   stockout_days / total_days if total_days else 0.0,
            "fill_rate":       total_fulfilled / total_demand if total_demand else 1.0,
            "waste_rate":      total_waste / total_ordered if total_ordered else 0.0,
            "waste_units":     total_waste,
            "stockout_units":  total_demand - total_fulfilled,
            "total_demand":    total_demand,
            "total_ordered":   total_ordered,
            "forecast_wape":   wape(actuals, forecasts),
            "forecast_mape":   mape(actuals, forecasts),
            "days_simulated":  total_days,
        }

    def records(self) -> List[DayRecord]:
        return list(self._records)
