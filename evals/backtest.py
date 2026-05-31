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
import math

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

# store_id -> (lat, lng)
StoreLocations = Dict[int, Tuple[float, float]]

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
# Geospatial helper
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Transfer simulation (cross-store redistribution)
# ---------------------------------------------------------------------------

TRANSFER_FIXED_COST = 20.0     # $ per trip
TRANSFER_DIST_COST  = 0.5      # $ per km
TRANSFER_UNIT_COST  = 0.10     # $ per unit moved
ITEM_WASTE_VALUE    = 2.00     # $ per unit wasted (simplified uniform)
MIN_TRANSFER_QTY    = 10       # don't bother for tiny quantities
MAX_TRANSFER_RADIUS = 20.0     # km


def _find_and_execute_transfers(
    inventory: Dict[Tuple[int, int], List[Batch]],
    today: date,
    store_locations: StoreLocations,
    shelf_lives: ShelfLives,
    forecasters: Dict[Tuple[int, int], BaseForecaster],
) -> Tuple[Dict[Tuple[int, int], List[Batch]], List[dict]]:
    """
    For each store-item with near-expiry excess, find the nearest store
    with genuine shortfall and execute the transfer if it's cheaper than waste.

    Returns (updated_inventory, transfer_log).
    """
    store_ids = sorted({sid for sid, _ in inventory})
    item_ids  = sorted({iid for _, iid in inventory})
    transfer_log: List[dict] = []

    for item_id in item_ids:
        shelf = shelf_lives.get(item_id, 7)
        expiry_horizon = max(1, shelf // 3)   # flag within 1/3 of shelf life

        # --- Find source stores: near-expiry excess ---
        sources = []
        for from_store in store_ids:
            key = (from_store, item_id)
            if key not in inventory:
                continue
            batches = inventory[key]
            near_exp_qty = sum(
                q for q, exp in batches
                if 0 < (exp - today).days <= expiry_horizon
            )
            if near_exp_qty < MIN_TRANSFER_QTY:
                continue
            fc = forecasters.get(key)
            expected_demand = fc.predict_total(expiry_horizon) if fc else near_exp_qty * 0.5
            transferable = max(0, near_exp_qty - expected_demand)
            if transferable >= MIN_TRANSFER_QTY:
                sources.append((from_store, int(transferable)))

        # --- Find sink stores: shortfall on this item ---
        sinks = []
        for to_store in store_ids:
            key = (to_store, item_id)
            if key not in inventory:
                continue
            total_qty = _total_inventory(inventory[key])
            fc = forecasters.get(key)
            demand_3d = fc.predict_total(3) if fc else 30.0
            shortfall = max(0.0, demand_3d - total_qty)
            if shortfall >= MIN_TRANSFER_QTY:
                sinks.append((to_store, int(shortfall)))

        # --- Match sources → nearest sink ---
        for from_store, transferable in sources:
            from_loc = store_locations.get(from_store)
            if not from_loc:
                continue

            # Sort sinks by largest shortfall first, break ties by distance
            ranked = []
            for to_store, shortfall in sinks:
                if to_store == from_store:
                    continue
                to_loc = store_locations.get(to_store)
                if not to_loc:
                    continue
                dist = haversine_km(*from_loc, *to_loc)
                if dist > MAX_TRANSFER_RADIUS:
                    continue
                ranked.append((to_store, shortfall, dist))

            ranked.sort(key=lambda x: (-x[1], x[2]))  # most shortfall, nearest

            for to_store, shortfall, dist_km in ranked:
                qty = min(transferable, shortfall)
                if qty < MIN_TRANSFER_QTY:
                    continue

                t_cost = TRANSFER_FIXED_COST + dist_km * TRANSFER_DIST_COST + qty * TRANSFER_UNIT_COST
                w_cost = qty * ITEM_WASTE_VALUE

                if t_cost >= w_cost:
                    continue  # not economical

                # Execute: move oldest (near-expiry) batches
                from_key = (from_store, item_id)
                to_key   = (to_store,   item_id)
                new_source = []
                moved = 0
                for q, exp in sorted(inventory[from_key], key=lambda x: x[1]):
                    if moved >= qty:
                        new_source.append((q, exp))
                        continue
                    take = min(q, qty - moved)
                    moved += take
                    leftover = q - take
                    if leftover > 0:
                        new_source.append((leftover, exp))
                    inventory[to_key].append((take, exp))  # preserve expiry

                inventory[from_key] = new_source
                transfer_log.append({
                    "from_store": from_store,
                    "to_store": to_store,
                    "item_id": item_id,
                    "quantity": moved,
                    "dist_km": round(dist_km, 1),
                    "cost_saved": round(w_cost - t_cost, 2),
                })
                break  # one transfer per source-item per day

    return inventory, transfer_log


# ---------------------------------------------------------------------------
# Agent-equivalent ordering logic (smarter than baseline)
# ---------------------------------------------------------------------------

def agent_compute_order_qty(
    batches: List[Batch],
    forecaster: BaseForecaster,
    horizon: int,
    shelf_life: int,
    delivery_weekdays: set,
    today: date,
) -> int:
    """
    The ordering logic the replenishment agent implements.

    Improvements over the baseline compute_order_qty:
    1. Expiry-aware: only counts inventory that won't expire before next delivery.
    2. Variance-based par: mean + 1.5σ × horizon (adapts to demand volatility).
    3. Hard shelf-life cap: never order more than shelf_life days of forecast demand.
    """
    # Effective inventory: only batches surviving to next delivery
    next_delivery = today + timedelta(days=horizon)
    effective_inv = sum(q for q, exp in batches if exp >= next_delivery)

    # Forecast demand to next delivery
    preds = forecaster.predict(horizon)
    forecast_total = sum(preds)

    # Adaptive par: mean + 1.5σ of predictions as safety cushion
    import numpy as np
    pred_arr = np.array(preds)
    adaptive_par = max(1, int((pred_arr.mean() + 1.5 * pred_arr.std()) * horizon))

    # How much we need to order to cover forecast + safety
    shortfall = adaptive_par - (effective_inv - forecast_total)
    if shortfall <= 0:
        return 0

    # Hard cap: never order more than will sell in shelf_life days
    sellable = forecaster.predict_total(min(shelf_life, 14))
    order_qty = min(shortfall, max(0, sellable - effective_inv))
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


def run_agent_backtest(
    demand_data: DemandData,
    delivery_schedules: DeliverySchedule,
    shelf_lives: ShelfLives,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """
    Backtest using the agent's smarter ordering logic (expiry-aware,
    variance-based safety stock). Uses SeasonalNaive forecaster.
    No LLM calls — deterministic simulation of what the agent would decide.
    """
    from forecasting.forecasters import SeasonalNaiveForecaster

    if config is None:
        config = BacktestConfig()

    accumulator = ScorecardAccumulator()

    all_dates: set[date] = set()
    for series in demand_data.values():
        all_dates.update(d for d, _ in series)
    sorted_dates = sorted(all_dates)

    train_end_idx = len(sorted_dates) - config.test_days - 1
    test_dates = sorted_dates[train_end_idx + 1:]

    # Seed inventory and forecasters
    forecasters: Dict[Tuple[int, int], BaseForecaster] = {}
    inventory: Dict[Tuple[int, int], List[Batch]] = {}

    for (store_id, item_id), series in demand_data.items():
        shelf = shelf_lives.get(item_id, 7)
        import numpy as np
        qtys = [q for _, q in series[:config.train_days]]
        mean = np.mean(qtys) if qtys else 30
        std  = np.std(qtys) if qtys else 5
        gap  = 7 // max(1, len(delivery_schedules.get(store_id, {0, 3})))
        seed_par = max(1, int((mean + 1.5 * std) * gap))
        seed_qty = int(seed_par * 0.7)
        seed_expiry = sorted_dates[train_end_idx] + timedelta(days=shelf)
        inventory[(store_id, item_id)] = [(seed_qty, seed_expiry)]
        forecasters[(store_id, item_id)] = SeasonalNaiveForecaster(k=4)

    demand_lookup: Dict[Tuple[int, int], Dict[date, int]] = {
        key: {d: q for d, q in series}
        for key, series in demand_data.items()
    }

    for today in test_dates:
        for (store_id, item_id), batches in inventory.items():
            shelf = shelf_lives.get(item_id, 7)
            series = demand_data[(store_id, item_id)]
            delivery_wds = delivery_schedules.get(store_id, {0, 3})

            history = [(d, q) for d, q in series if d < today]
            hist_dates = [d for d, _ in history]
            hist_qtys  = [q for _, q in history]

            fc = forecasters[(store_id, item_id)]
            fc.fit(hist_dates, hist_qtys)
            one_day_forecast = fc.predict(1)[0] if hist_dates else 0.0

            batches, wasted = _expire_batches(batches, today)
            actual_demand = demand_lookup[(store_id, item_id)].get(today, 0)
            batches, fulfilled = _consume(batches, actual_demand)

            ordered = 0
            if today.weekday() in delivery_wds:
                horizon = _days_to_next_delivery(today, delivery_wds)
                order_qty = agent_compute_order_qty(
                    batches, fc, horizon, shelf, delivery_wds, today
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

    return BacktestResult(
        scorecard=accumulator.scorecard(),
        records=accumulator.records(),
        forecaster_name="ReplenishmentAgent (expiry-aware, variance-based par)",
        config=config,
    )


def run_phase4_backtest(
    demand_data: DemandData,
    delivery_schedules: DeliverySchedule,
    shelf_lives: ShelfLives,
    store_locations: StoreLocations,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """
    Phase 4: agent ordering logic + cross-location transfers.

    Each day:
      1. Expire stale batches.
      2. Consume demand.
      3. Execute transfers (near-expiry excess → nearby shortfall stores).
      4. Place orders on delivery days.

    Done when: transfers measurably reduce waste vs Phase 3 without
    raising stockouts.
    """
    from forecasting.forecasters import SeasonalNaiveForecaster

    if config is None:
        config = BacktestConfig()

    accumulator = ScorecardAccumulator()
    transfer_count = 0
    units_saved_by_transfer = 0

    all_dates: set[date] = set()
    for series in demand_data.values():
        all_dates.update(d for d, _ in series)
    sorted_dates = sorted(all_dates)

    train_end_idx = len(sorted_dates) - config.test_days - 1
    test_dates = sorted_dates[train_end_idx + 1:]

    forecasters: Dict[Tuple[int, int], BaseForecaster] = {}
    inventory: Dict[Tuple[int, int], List[Batch]] = {}

    import numpy as np
    for (store_id, item_id), series in demand_data.items():
        shelf = shelf_lives.get(item_id, 7)
        qtys = [q for _, q in series[:config.train_days]]
        mean = np.mean(qtys) if qtys else 30
        std  = np.std(qtys)  if qtys else 5
        gap  = 7 // max(1, len(delivery_schedules.get(store_id, {0, 3})))
        seed_par = max(1, int((mean + 1.5 * std) * gap))
        seed_qty = int(seed_par * 0.7)
        seed_expiry = sorted_dates[train_end_idx] + timedelta(days=shelf)
        inventory[(store_id, item_id)] = [(seed_qty, seed_expiry)]
        forecasters[(store_id, item_id)] = SeasonalNaiveForecaster(k=4)

    demand_lookup: Dict[Tuple[int, int], Dict[date, int]] = {
        key: {d: q for d, q in series}
        for key, series in demand_data.items()
    }

    # Pre-fit snapshot: will be updated each day
    for today in test_dates:
        # Update forecasters with all history up to (not including) today
        for key, series in demand_data.items():
            history = [(d, q) for d, q in series if d < today]
            if history:
                forecasters[key].fit(
                    [d for d, _ in history],
                    [q for _, q in history],
                )

        # 1. Expire
        for key in list(inventory):
            inventory[key], _ = _expire_batches(inventory[key], today)

        # 2. Consume demand
        fulfilled_map: Dict[Tuple[int, int], int] = {}
        wasted_map:   Dict[Tuple[int, int], int] = {}
        demand_map:   Dict[Tuple[int, int], int] = {}
        for key in inventory:
            actual = demand_lookup[key].get(today, 0)
            demand_map[key] = actual
            # Re-expire before consume (already done above, but track waste)
            batches, wasted = _expire_batches(inventory[key], today)
            wasted_map[key] = wasted
            batches, fulfilled = _consume(batches, actual)
            fulfilled_map[key] = fulfilled
            inventory[key] = batches

        # 3. Transfers (cross-store, before ordering)
        inventory, transfer_log = _find_and_execute_transfers(
            inventory, today, store_locations, shelf_lives, forecasters
        )
        transfer_count += len(transfer_log)
        units_saved_by_transfer += sum(t["cost_saved"] / ITEM_WASTE_VALUE
                                       for t in transfer_log)

        # 4. Order on delivery days
        ordered_map: Dict[Tuple[int, int], int] = {}
        for (store_id, item_id), batches in inventory.items():
            shelf = shelf_lives.get(item_id, 7)
            delivery_wds = delivery_schedules.get(store_id, {0, 3})
            ordered = 0
            if today.weekday() in delivery_wds:
                horizon = _days_to_next_delivery(today, delivery_wds)
                fc = forecasters[(store_id, item_id)]
                order_qty = agent_compute_order_qty(
                    batches, fc, horizon, shelf, delivery_wds, today
                )
                if order_qty > 0:
                    expiry = today + timedelta(days=shelf)
                    batches.append((order_qty, expiry))
                    ordered = order_qty
            ordered_map[(store_id, item_id)] = ordered
            inventory[(store_id, item_id)] = batches

        # Record
        for key in inventory:
            store_id, item_id = key
            fc = forecasters[key]
            one_day_fc = fc.predict(1)[0]
            accumulator.record(DayRecord(
                date=today,
                store_id=store_id,
                item_id=item_id,
                demand=demand_map.get(key, 0),
                fulfilled=fulfilled_map.get(key, 0),
                waste=wasted_map.get(key, 0),
                ordered=ordered_map.get(key, 0),
                forecast=one_day_fc,
                inventory_eod=_total_inventory(inventory[key]),
            ))

    sc = accumulator.scorecard()
    sc["transfers_executed"] = transfer_count
    sc["units_rescued_est"]  = int(units_saved_by_transfer)

    return BacktestResult(
        scorecard=sc,
        records=accumulator.records(),
        forecaster_name="Phase 4: Agent + Cross-location Transfers",
        config=config,
    )
