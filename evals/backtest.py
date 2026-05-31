"""
Backtest harness.

Replays historical demand day by day. At each step the forecaster and
ordering logic only see data up to that day — never future demand.

Inventory model:
  - FIFO batches: list of (quantity, expiry_date) tuples, oldest first.
  - Each day: expire stale batches → consume demand → (on delivery day) order.
  - Ordering logic: forecast demand to next delivery, order up to par,
    capped by shelf life (don't order more than will sell before expiry).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Tuple, Type
import copy

from forecasting.forecasters import BaseForecaster
from evals.metrics import ScorecardAccumulator, DayRecord


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# (store_id, item_id) -> list of (date, actual_demand)
DemandData = Dict[Tuple[int, int], List[Tuple[date, int]]]

# store_id -> set of weekday ints (0=Mon)
DeliverySchedule = Dict[int, set]

# (store_id, item_id) -> par level
ParLevels = Dict[Tuple[int, int], int]

# item_id -> shelf_life_days
ShelfLives = Dict[int, int]

# Inventory batch: (quantity, expiry_date)
Batch = Tuple[int, date]


# ---------------------------------------------------------------------------
# Inventory helpers
# ---------------------------------------------------------------------------

def _expire_batches(
    batches: List[Batch], today: date
) -> Tuple[List[Batch], int]:
    """Remove expired batches. Returns (remaining, units_wasted)."""
    live = []
    wasted = 0
    for qty, exp in batches:
        if exp < today:
            wasted += qty
        else:
            live.append((qty, exp))
    return live, wasted


def _consume(batches: List[Batch], demand: int) -> Tuple[List[Batch], int]:
    """
    Consume `demand` units FIFO (oldest batch first).
    Returns (remaining_batches, units_fulfilled).
    """
    remaining = []
    fulfilled = 0
    for qty, exp in batches:
        if fulfilled >= demand:
            remaining.append((qty, exp))
            continue
        take = min(qty, demand - fulfilled)
        fulfilled += take
        leftover = qty - take
        if leftover > 0:
            remaining.append((leftover, exp))
    return remaining, fulfilled


def _total_inventory(batches: List[Batch]) -> int:
    return sum(q for q, _ in batches)


def _days_to_next_delivery(today: date, delivery_weekdays: set) -> int:
    """Days until next delivery from today (at least 1)."""
    for delta in range(1, 8):
        candidate = today + timedelta(days=delta)
        if candidate.weekday() in delivery_weekdays:
            return delta
    return 7  # fallback


# ---------------------------------------------------------------------------
# Ordering logic (pure function — no side effects)
# ---------------------------------------------------------------------------

def compute_order_qty(
    current_inventory: int,
    forecaster: BaseForecaster,
    horizon: int,
    par: int,
    shelf_life: int,
) -> int:
    """
    Order enough to reach par, adjusted for forecast.
    Don't order more than can sell before shelf life expires.
    """
    forecast_demand = forecaster.predict_total(horizon)
    # Expected inventory at next delivery
    expected_eod = max(0, current_inventory - forecast_demand)
    # Gap to fill
    shortfall = par - expected_eod
    if shortfall <= 0:
        return 0
    # Cap by what can realistically sell before the shelf expires
    sellable_cap = forecaster.predict_total(shelf_life)
    order_qty = min(shortfall, max(0, sellable_cap - current_inventory))
    return max(0, int(round(order_qty)))


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    train_days: int = 60      # days of history available before test starts
    test_days: int = 60       # days to simulate
    safety_stock_days: int = 1


@dataclass
class BacktestResult:
    scorecard: dict
    records: list            # List[DayRecord]
    forecaster_name: str
    config: BacktestConfig


def run_backtest(
    forecaster_cls: Type[BaseForecaster],
    forecaster_kwargs: dict,
    demand_data: DemandData,
    delivery_schedules: DeliverySchedule,
    par_levels: ParLevels,
    shelf_lives: ShelfLives,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """
    Run the full backtest and return a scored result.

    `demand_data` must cover at least train_days + test_days of history.
    The engine never peeks beyond the current simulation day.
    """
    if config is None:
        config = BacktestConfig()

    accumulator = ScorecardAccumulator()

    # Determine the common date range across all store-item pairs
    all_dates: set[date] = set()
    for series in demand_data.values():
        all_dates.update(d for d, _ in series)

    sorted_dates = sorted(all_dates)
    if len(sorted_dates) < config.train_days + config.test_days:
        raise ValueError(
            f"Not enough data: need {config.train_days + config.test_days} days, "
            f"got {len(sorted_dates)}"
        )

    # Split into train cutoff and test window
    train_end_idx = len(sorted_dates) - config.test_days - 1
    test_dates = sorted_dates[train_end_idx + 1:]

    # Initialise one forecaster instance and inventory per store-item pair
    forecasters: Dict[Tuple[int, int], BaseForecaster] = {}
    inventory: Dict[Tuple[int, int], List[Batch]] = {}

    for (store_id, item_id), series in demand_data.items():
        # Seed inventory: estimate from par level at train_end_idx
        par = par_levels.get((store_id, item_id), 50)
        shelf = shelf_lives.get(item_id, 7)
        seed_qty = int(par * 0.7)
        seed_expiry = sorted_dates[train_end_idx] + timedelta(days=shelf)
        inventory[(store_id, item_id)] = [(seed_qty, seed_expiry)]
        forecasters[(store_id, item_id)] = forecaster_cls(**forecaster_kwargs)

    # Build a lookup: (store_id, item_id) -> {date: quantity}
    demand_lookup: Dict[Tuple[int, int], Dict[date, int]] = {
        key: {d: q for d, q in series}
        for key, series in demand_data.items()
    }

    # --- Simulate day by day ---
    for today in test_dates:
        for (store_id, item_id), batches in inventory.items():
            shelf = shelf_lives.get(item_id, 7)
            par   = par_levels.get((store_id, item_id), 50)
            series = demand_data[(store_id, item_id)]

            # Only use demand up to (but NOT including) today
            history = [(d, q) for d, q in series if d < today]
            hist_dates = [d for d, _ in history]
            hist_qtys  = [q for _, q in history]

            # Fit forecaster on history
            fc = forecasters[(store_id, item_id)]
            fc.fit(hist_dates, hist_qtys)

            # Today's one-day forecast (for the DayRecord)
            one_day_forecast = fc.predict(1)[0] if hist_dates else 0.0

            # 1. Expire stale inventory
            batches, wasted = _expire_batches(batches, today)

            # 2. Consume today's actual demand
            actual_demand = demand_lookup[(store_id, item_id)].get(today, 0)
            batches, fulfilled = _consume(batches, actual_demand)

            # 3. Order on delivery days
            delivery_wds = delivery_schedules.get(store_id, {0, 3})
            ordered = 0
            if today.weekday() in delivery_wds:
                horizon = _days_to_next_delivery(today, delivery_wds)
                current_inv = _total_inventory(batches)
                order_qty = compute_order_qty(
                    current_inv, fc, horizon, par, shelf
                )
                if order_qty > 0:
                    expiry = today + timedelta(days=shelf)
                    batches.append((order_qty, expiry))
                    ordered = order_qty

            inventory[(store_id, item_id)] = batches

            accumulator.record(DayRecord(
                date=today,
                store_id=store_id,
                item_id=item_id,
                demand=actual_demand,
                fulfilled=fulfilled,
                waste=wasted,
                ordered=ordered,
                forecast=one_day_forecast,
                inventory_eod=_total_inventory(batches),
            ))

    forecaster_name = repr(forecaster_cls(**forecaster_kwargs))
    return BacktestResult(
        scorecard=accumulator.scorecard(),
        records=accumulator.records(),
        forecaster_name=forecaster_name,
        config=config,
    )
