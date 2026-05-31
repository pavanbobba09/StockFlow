"""
Backtest runner — loads demand data from Postgres and scores forecasters.

Usage:
    python -m evals.run_backtest
    python -m evals.run_backtest --train-days 90 --test-days 60
    python -m evals.run_backtest --store-ids 1,2,3 --item-ids 1,2
"""

import argparse
import sys
from datetime import date
from typing import Dict, List, Tuple

from sqlalchemy import text

from data.db import SessionLocal
from evals.backtest import (
    BacktestConfig, BacktestResult, DemandData,
    DeliverySchedule, ParLevels, ShelfLives,
    run_backtest, run_agent_backtest,
)
from forecasting.forecasters import MovingAverageForecaster, SeasonalNaiveForecaster


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_demand_data(
    session,
    store_ids: List[int] | None,
    item_ids: List[int] | None,
) -> DemandData:
    where = "WHERE 1=1"
    params: dict = {}
    if store_ids:
        where += " AND store_id = ANY(:sids)"
        params["sids"] = store_ids
    if item_ids:
        where += " AND item_id = ANY(:iids)"
        params["iids"] = item_ids

    rows = session.execute(
        text(f"SELECT store_id, item_id, date, quantity FROM demand_history {where} ORDER BY date"),
        params,
    ).fetchall()

    data: DemandData = {}
    for r in rows:
        key = (r.store_id, r.item_id)
        data.setdefault(key, []).append((r.date, r.quantity))
    return data


def load_delivery_schedules(session, store_ids: List[int] | None) -> DeliverySchedule:
    where = ""
    params: dict = {}
    if store_ids:
        where = "WHERE store_id = ANY(:sids)"
        params["sids"] = store_ids
    rows = session.execute(
        text(f"SELECT store_id, weekday FROM delivery_schedules {where}"),
        params,
    ).fetchall()
    sched: DeliverySchedule = {}
    for r in rows:
        sched.setdefault(r.store_id, set()).add(r.weekday)
    return sched


def load_par_levels(session, demand_data: DemandData) -> ParLevels:
    """
    Approximate par levels from demand history:
    par = 95th-percentile daily demand × avg delivery gap × 1.3 safety factor.
    """
    import numpy as np
    par: ParLevels = {}
    for (store_id, item_id), series in demand_data.items():
        qtys = [q for _, q in series]
        p95 = float(np.percentile(qtys, 95))
        par[(store_id, item_id)] = max(1, int(p95 * 3.5 * 1.3))
    return par


def load_shelf_lives(session, item_ids: List[int] | None) -> ShelfLives:
    where = ""
    params: dict = {}
    if item_ids:
        where = "WHERE id = ANY(:iids)"
        params["iids"] = item_ids
    rows = session.execute(
        text(f"SELECT id, shelf_life_days FROM items {where}"),
        params,
    ).fetchall()
    return {r.id: r.shelf_life_days for r in rows}


# ---------------------------------------------------------------------------
# Pretty-print scorecard
# ---------------------------------------------------------------------------

def print_scorecard(result: BacktestResult) -> None:
    sc = result.scorecard
    print(f"\n{'='*55}")
    print(f"  Forecaster : {result.forecaster_name}")
    print(f"  Test days  : {result.config.test_days}")
    print(f"  Train days : {result.config.train_days}")
    print(f"{'='*55}")
    print(f"  Days simulated   : {sc['days_simulated']:>10,}")
    print(f"  Total demand     : {sc['total_demand']:>10,} units")
    print(f"  Total ordered    : {sc['total_ordered']:>10,} units")
    print()
    print(f"  Fill rate        : {sc['fill_rate']*100:>9.1f}%   (higher better)")
    print(f"  Stockout rate    : {sc['stockout_rate']*100:>9.1f}%   (lower better)")
    print(f"  Stockout units   : {sc['stockout_units']:>10,}")
    print()
    print(f"  Waste rate       : {sc['waste_rate']*100:>9.1f}%   (lower better)")
    print(f"  Waste units      : {sc['waste_units']:>10,}")
    print()
    print(f"  Forecast WAPE    : {sc['forecast_wape']*100:>9.1f}%   (lower better)")
    print(f"  Forecast MAPE    : {sc['forecast_mape']*100:>9.1f}%   (lower better)")
    print(f"{'='*55}\n")


def compare_results(results: List[BacktestResult]) -> None:
    """Print a side-by-side comparison table."""
    metrics = [
        ("fill_rate",      "Fill rate",      True,  "%"),
        ("stockout_rate",  "Stockout rate",  False, "%"),
        ("waste_rate",     "Waste rate",     False, "%"),
        ("forecast_wape",  "WAPE",           False, "%"),
    ]
    header = f"{'Metric':<20}" + "".join(f"{r.forecaster_name:<30}" for r in results)
    print("\n" + "="*80)
    print("  COMPARISON")
    print("="*80)
    print(f"  {'Metric':<22}" + "".join(f"  {r.forecaster_name:<28}" for r in results))
    print("-"*80)
    for key, label, higher_better, unit in metrics:
        row = f"  {label:<22}"
        for r in results:
            val = r.scorecard[key] * 100
            row += f"  {val:>6.1f}{unit:<22}"
        row += "  ↑ better" if higher_better else "  ↓ better"
        print(row)
    print("="*80 + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run StockFlow backtest")
    parser.add_argument("--train-days", type=int, default=90)
    parser.add_argument("--test-days",  type=int, default=60)
    parser.add_argument("--store-ids",  type=str, default=None,
                        help="Comma-separated store IDs (default: all)")
    parser.add_argument("--item-ids",   type=str, default=None,
                        help="Comma-separated item IDs (default: all)")
    args = parser.parse_args()

    store_ids = [int(x) for x in args.store_ids.split(",")] if args.store_ids else None
    item_ids  = [int(x) for x in args.item_ids.split(",")]  if args.item_ids  else None

    config = BacktestConfig(
        train_days=args.train_days,
        test_days=args.test_days,
    )

    print("Loading data from Postgres...")
    session = SessionLocal()
    try:
        demand_data       = load_demand_data(session, store_ids, item_ids)
        delivery_schedules = load_delivery_schedules(session, store_ids)
        par_levels        = load_par_levels(session, demand_data)
        shelf_lives       = load_shelf_lives(session, item_ids)
    finally:
        session.close()

    n_pairs = len(demand_data)
    print(f"  {n_pairs} store-item pairs loaded.")

    # Phase 2 baselines
    forecasters = [
        (MovingAverageForecaster, {"window": 14}),
        (SeasonalNaiveForecaster, {"k": 4}),
    ]

    results = []
    for cls, kwargs in forecasters:
        name = repr(cls(**kwargs))
        print(f"\nRunning backtest — {name} ...")
        result = run_backtest(
            forecaster_cls=cls,
            forecaster_kwargs=kwargs,
            demand_data=demand_data,
            delivery_schedules=delivery_schedules,
            par_levels=par_levels,
            shelf_lives=shelf_lives,
            config=config,
        )
        print_scorecard(result)
        results.append(result)

    # Phase 3: agent-equivalent policy (expiry-aware, variance-based par)
    print("\nRunning backtest — ReplenishmentAgent policy ...")
    agent_result = run_agent_backtest(
        demand_data=demand_data,
        delivery_schedules=delivery_schedules,
        shelf_lives=shelf_lives,
        config=config,
    )
    print_scorecard(agent_result)
    results.append(agent_result)

    compare_results(results)
    print("Backtest complete.")


if __name__ == "__main__":
    main()
