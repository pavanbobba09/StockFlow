"""
Deterministic AI-agent simulator for the recruiter-facing StockFlow demo.

This module intentionally does not call an LLM. It models the agents as
auditable decision services so the demo works from a fresh seed with no API key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
import math
import os
import random
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agents.live_signals import get_live_signal_summary


CHAIN_NAME = "QuickBite Franchise Network"
UNIT_VALUE = 2.0
TRANSFER_FIXED_COST = 18.0
TRANSFER_KM_COST = 0.6
TRANSFER_UNIT_COST = 0.08
DEFAULT_SCENARIO = "weekend-rush"


SCENARIOS = {
    "weekend-rush": {
        "label": "Weekend Rush",
        "demand_multiplier": 1.65,
        "description": "Friday-to-Sunday customer traffic rises across the franchise network.",
    },
    "game-day-spike": {
        "label": "Game Day Spike",
        "demand_multiplier": 2.15,
        "description": "Downtown and stadium-adjacent stores see sudden demand spikes.",
    },
    "delivery-delay": {
        "label": "Delivery Delay",
        "demand_multiplier": 1.25,
        "description": "Next supplier truck is delayed, so agents must stretch inventory.",
    },
    "expiry-rescue": {
        "label": "Expiry Rescue",
        "demand_multiplier": 0.95,
        "description": "Some stores have excess food expiring soon while others are short.",
    },
    "store-to-store-transfer": {
        "label": "Store-to-Store Transfer",
        "demand_multiplier": 1.1,
        "description": "Agents coordinate a nearby transfer before placing a supplier order.",
    },
}


DEMO_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS demo_state (
        key VARCHAR(100) PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS demo_inventory_baseline (
        id SERIAL PRIMARY KEY,
        location_id INTEGER NOT NULL,
        location_type VARCHAR(50) NOT NULL CHECK (location_type IN ('store', 'warehouse')),
        item_id INTEGER NOT NULL REFERENCES items(id),
        quantity INTEGER NOT NULL,
        expiry_date DATE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS simulation_ticks (
        id SERIAL PRIMARY KEY,
        sim_day INTEGER NOT NULL,
        sim_date DATE NOT NULL,
        scenario VARCHAR(100) NOT NULL,
        status VARCHAR(50) NOT NULL DEFAULT 'completed',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_events (
        id SERIAL PRIMARY KEY,
        tick_id INTEGER REFERENCES simulation_ticks(id),
        agent_name VARCHAR(100) NOT NULL,
        event_type VARCHAR(100) NOT NULL,
        store_id INTEGER REFERENCES stores(id),
        item_id INTEGER REFERENCES items(id),
        severity VARCHAR(50) NOT NULL DEFAULT 'info',
        message TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_reasoning_traces (
        id SERIAL PRIMARY KEY,
        tick_id INTEGER REFERENCES simulation_ticks(id),
        agent_name VARCHAR(100) NOT NULL,
        tool_name VARCHAR(100) NOT NULL,
        input_summary TEXT NOT NULL,
        observation TEXT NOT NULL,
        decision TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_reasoning_traces_tick ON agent_reasoning_traces(tick_id)",
    """
    CREATE TABLE IF NOT EXISTS agent_decisions (
        id SERIAL PRIMARY KEY,
        tick_id INTEGER REFERENCES simulation_ticks(id),
        decision_type VARCHAR(50) NOT NULL CHECK (decision_type IN ('order', 'transfer', 'markdown', 'donation')),
        agent_name VARCHAR(100) NOT NULL,
        store_id INTEGER REFERENCES stores(id),
        target_store_id INTEGER REFERENCES stores(id),
        item_id INTEGER REFERENCES items(id),
        quantity INTEGER NOT NULL,
        status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
        idempotency_key VARCHAR(255) NOT NULL UNIQUE,
        reason TEXT NOT NULL,
        expected_impact TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        decided_at TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_decisions_status ON agent_decisions(status)",
    "CREATE INDEX IF NOT EXISTS idx_agent_decisions_type ON agent_decisions(decision_type)",
    """
    CREATE TABLE IF NOT EXISTS approval_events (
        id SERIAL PRIMARY KEY,
        decision_id INTEGER NOT NULL REFERENCES agent_decisions(id),
        action VARCHAR(50) NOT NULL CHECK (action IN ('approved', 'rejected')),
        idempotency_key VARCHAR(255) NOT NULL UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory_movements (
        id SERIAL PRIMARY KEY,
        decision_id INTEGER REFERENCES agent_decisions(id),
        movement_type VARCHAR(50) NOT NULL,
        from_location_id INTEGER,
        from_location_type VARCHAR(50),
        to_location_id INTEGER,
        to_location_type VARCHAR(50),
        item_id INTEGER NOT NULL REFERENCES items(id),
        quantity INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS simulated_demand_events (
        id SERIAL PRIMARY KEY,
        tick_id INTEGER REFERENCES simulation_ticks(id),
        store_id INTEGER NOT NULL REFERENCES stores(id),
        item_id INTEGER NOT NULL REFERENCES items(id),
        demand INTEGER NOT NULL,
        fulfilled INTEGER NOT NULL,
        stockout_units INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS waste_events (
        id SERIAL PRIMARY KEY,
        tick_id INTEGER REFERENCES simulation_ticks(id),
        store_id INTEGER NOT NULL REFERENCES stores(id),
        item_id INTEGER NOT NULL REFERENCES items(id),
        quantity INTEGER NOT NULL,
        reason VARCHAR(100) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]


@dataclass(frozen=True)
class DemandResult:
    demand: int
    fulfilled: int
    stockout_units: int


def ensure_demo_schema(db: Session) -> None:
    for ddl in DEMO_SCHEMA_SQL:
        db.execute(text(ddl))
    baseline_count = db.execute(text("SELECT COUNT(*) FROM demo_inventory_baseline")).scalar() or 0
    if baseline_count == 0:
        db.execute(
            text("""
                INSERT INTO demo_inventory_baseline
                    (location_id, location_type, item_id, quantity, expiry_date)
                SELECT location_id, location_type, item_id, quantity, expiry_date
                FROM inventory
            """)
        )
    _set_state(db, "chain_name", CHAIN_NAME)
    if _get_state(db, "sim_day") is None:
        _set_state(db, "sim_day", "0")
    if _get_state(db, "autoplay", None) is None:
        _set_state(db, "autoplay", "false")
    if _get_state(db, "scenario", None) is None:
        _set_state(db, "scenario", DEFAULT_SCENARIO)
    db.commit()


def reset_demo(db: Session) -> dict[str, Any]:
    ensure_demo_schema(db)
    for table in [
        "approval_events",
        "inventory_movements",
        "agent_decisions",
        "agent_reasoning_traces",
        "agent_events",
        "simulated_demand_events",
        "waste_events",
        "simulation_ticks",
    ]:
        db.execute(text(f"DELETE FROM {table}"))
    db.execute(text("DELETE FROM transfers"))
    db.execute(text("DELETE FROM orders"))
    db.execute(text("DELETE FROM inventory"))
    db.execute(
        text("""
            INSERT INTO inventory (location_id, location_type, item_id, quantity, expiry_date)
            SELECT
                b.location_id,
                b.location_type,
                b.item_id,
                b.quantity,
                CASE
                    WHEN b.expiry_date IS NULL THEN NULL
                    WHEN b.location_type = 'warehouse' THEN CURRENT_DATE + GREATEST(i.shelf_life_days + 7, 14)
                    ELSE CURRENT_DATE + GREATEST(1, ((b.id % GREATEST(i.shelf_life_days, 1)) + 1))
                END AS expiry_date
            FROM demo_inventory_baseline b
            JOIN items i ON i.id = b.item_id
        """)
    )
    _set_state(db, "sim_day", "0")
    _set_state(db, "autoplay", "false")
    _set_state(db, "scenario", DEFAULT_SCENARIO)
    db.commit()
    _emit_event(
        db,
        tick_id=None,
        agent_name="Manager Approval Agent",
        event_type="demo_reset",
        severity="info",
        message="Demo reset to the saved synthetic franchise baseline.",
    )
    db.commit()
    return get_demo_state(db)


def set_autoplay(db: Session, enabled: bool) -> dict[str, Any]:
    ensure_demo_schema(db)
    _set_state(db, "autoplay", "true" if enabled else "false")
    _emit_event(
        db,
        None,
        "Manager Approval Agent",
        "autoplay",
        "info",
        f"Autoplay {'started' if enabled else 'paused'} for the live agent simulation.",
    )
    db.commit()
    return get_demo_state(db)


def apply_scenario(db: Session, scenario_name: str) -> dict[str, Any]:
    ensure_demo_schema(db)
    if scenario_name not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario_name}")
    _set_state(db, "scenario", scenario_name)
    if scenario_name in {"expiry-rescue", "store-to-store-transfer"}:
        _prime_transfer_scenario(db)
    _emit_event(
        db,
        None,
        "Demand Forecast Agent",
        "scenario_loaded",
        "info",
        f"Loaded {SCENARIOS[scenario_name]['label']}: {SCENARIOS[scenario_name]['description']}",
    )
    db.commit()
    return get_demo_state(db)


def run_demo_tick(db: Session) -> dict[str, Any]:
    ensure_demo_schema(db)
    current_day = int(_get_state(db, "sim_day", "0"))
    sim_day = current_day + 1
    sim_date = date.today() + timedelta(days=sim_day)
    scenario = _get_state(db, "scenario", DEFAULT_SCENARIO)
    scenario_cfg = SCENARIOS.get(scenario, SCENARIOS[DEFAULT_SCENARIO])
    live_signals = get_live_signal_summary(db)
    multiplier = round(float(scenario_cfg["demand_multiplier"]) * float(live_signals["demand_multiplier"]), 3)

    tick_id = db.execute(
        text("""
            INSERT INTO simulation_ticks (sim_day, sim_date, scenario)
            VALUES (:day, :date, :scenario)
            RETURNING id
        """),
        {"day": sim_day, "date": sim_date, "scenario": scenario},
    ).scalar()

    _emit_event(
        db,
        tick_id,
        "Demand Forecast Agent",
        "tick_started",
        "info",
        (
            f"Day {sim_day} started under {scenario_cfg['label']}; effective demand multiplier is "
            f"{multiplier:.2f}x after live signals."
        ),
    )
    _emit_event(
        db,
        tick_id,
        "Live Signal Agent",
        "free_api_signals",
        "info",
        " ".join(live_signals.get("reasons", [])),
    )

    _expire_inventory(db, tick_id, sim_date)
    _simulate_customer_demand(db, tick_id, sim_day, multiplier)
    from agents.langgraph_decision_engine import run_langgraph_decision_engine

    graph_result = run_langgraph_decision_engine(db, tick_id, scenario)
    _emit_event(
        db,
        tick_id,
        "LangGraph Orchestrator",
        "graph_complete",
        "info",
        (
            "Ran Inventory Watcher -> Demand Forecast -> Transfer/Waste -> "
            f"Replenishment -> Baseline Scorer with {graph_result.get('trace_count', 0)} reasoning traces."
        ),
    )
    _emit_manager_summary(db, tick_id)

    _set_state(db, "sim_day", str(sim_day))
    db.commit()
    return get_demo_state(db)


def approve_decision(db: Session, decision_id: int) -> dict[str, Any]:
    return _decide(db, decision_id, "approved")


def reject_decision(db: Session, decision_id: int) -> dict[str, Any]:
    return _decide(db, decision_id, "rejected")


def get_demo_state(db: Session) -> dict[str, Any]:
    ensure_demo_schema(db)
    scenario = _get_state(db, "scenario", DEFAULT_SCENARIO)
    sim_day = int(_get_state(db, "sim_day", "0"))
    live_signals = get_live_signal_summary(db)
    return {
        "chain_name": CHAIN_NAME,
        "sim_day": sim_day,
        "sim_date": str(date.today() + timedelta(days=sim_day)),
        "autoplay": _get_state(db, "autoplay", "false") == "true",
        "simulation_speed_ms": int(os.getenv("SIMULATION_SPEED_MS", "4500")),
        "scenario": {"name": scenario, **SCENARIOS.get(scenario, SCENARIOS[DEFAULT_SCENARIO])},
        "live_signals": live_signals,
        "agents": _agent_roster(),
        "restaurants": _restaurants(db),
        "warehouses": _warehouses(db),
        "events": get_agent_events(db, limit=40),
        "reasoning_traces": get_reasoning_traces(db, limit=40),
        "pending_decisions": get_pending_decisions(db),
        "routes": _routes(db),
        "metrics": demo_impact_metrics(db),
    }


def get_agent_events(db: Session, limit: int = 50) -> list[dict[str, Any]]:
    ensure_demo_schema(db)
    rows = db.execute(
        text("""
            SELECT
                ae.id, ae.tick_id, ae.agent_name, ae.event_type, ae.severity,
                ae.message, ae.created_at, s.name AS store_name, i.name AS item_name
            FROM agent_events ae
            LEFT JOIN stores s ON s.id = ae.store_id
            LEFT JOIN items i ON i.id = ae.item_id
            ORDER BY ae.created_at DESC, ae.id DESC
            LIMIT :limit
        """),
        {"limit": limit},
    ).fetchall()
    return [
        {
            "id": r.id,
            "tick_id": r.tick_id,
            "agent_name": r.agent_name,
            "event_type": r.event_type,
            "severity": r.severity,
            "message": r.message,
            "store_name": r.store_name,
            "item_name": r.item_name,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def get_reasoning_traces(db: Session, limit: int = 50) -> list[dict[str, Any]]:
    ensure_demo_schema(db)
    rows = db.execute(
        text("""
            SELECT
                id, tick_id, agent_name, tool_name, input_summary,
                observation, decision, created_at
            FROM agent_reasoning_traces
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
        """),
        {"limit": limit},
    ).fetchall()
    return [
        {
            "id": r.id,
            "tick_id": r.tick_id,
            "agent_name": r.agent_name,
            "tool_name": r.tool_name,
            "input_summary": r.input_summary,
            "observation": r.observation,
            "decision": r.decision,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def get_pending_decisions(db: Session) -> list[dict[str, Any]]:
    ensure_demo_schema(db)
    rows = db.execute(
        text("""
            SELECT
                d.id, d.tick_id, d.decision_type, d.agent_name, d.store_id,
                d.target_store_id, d.item_id, d.quantity, d.status, d.reason,
                d.expected_impact, d.idempotency_key, d.created_at,
                s.name AS store_name, ts.name AS target_store_name, i.name AS item_name
            FROM agent_decisions d
            LEFT JOIN stores s ON s.id = d.store_id
            LEFT JOIN stores ts ON ts.id = d.target_store_id
            LEFT JOIN items i ON i.id = d.item_id
            WHERE d.status = 'pending'
            ORDER BY d.created_at DESC, d.id DESC
        """)
    ).fetchall()
    return [_decision_row(r) for r in rows]


def demo_impact_metrics(db: Session) -> dict[str, Any]:
    ensure_demo_schema(db)
    demand = db.execute(
        text("""
            SELECT
                COALESCE(SUM(demand), 0) AS demand,
                COALESCE(SUM(fulfilled), 0) AS fulfilled,
                COALESCE(SUM(stockout_units), 0) AS stockouts
            FROM simulated_demand_events
        """)
    ).fetchone()
    waste_units = db.execute(text("SELECT COALESCE(SUM(quantity), 0) FROM waste_events")).scalar() or 0
    approved = db.execute(text("SELECT COUNT(*) FROM agent_decisions WHERE status = 'approved'")).scalar() or 0
    rejected = db.execute(text("SELECT COUNT(*) FROM agent_decisions WHERE status = 'rejected'")).scalar() or 0
    pending = db.execute(text("SELECT COUNT(*) FROM agent_decisions WHERE status = 'pending'")).scalar() or 0
    transfer_units = db.execute(
        text("""
            SELECT COALESCE(SUM(quantity), 0)
            FROM agent_decisions
            WHERE decision_type = 'transfer' AND status = 'approved'
        """)
    ).scalar() or 0
    order_units = db.execute(
        text("""
            SELECT COALESCE(SUM(quantity), 0)
            FROM agent_decisions
            WHERE decision_type = 'order' AND status IN ('pending', 'approved')
        """)
    ).scalar() or 0
    stockouts = int(demand.stockouts or 0)
    total_demand = int(demand.demand or 0)
    fulfilled = int(demand.fulfilled or 0)
    accepted_total = approved + rejected
    stockouts_avoided = int(transfer_units * 0.65 + approved * 8)
    waste_reduced = int(transfer_units * 0.8)
    estimated_profit_saved = round((stockouts_avoided * 3.5) + (waste_reduced * UNIT_VALUE), 2)
    return {
        "total_demand": total_demand,
        "fulfilled": fulfilled,
        "fill_rate": round(fulfilled / total_demand, 3) if total_demand else 1.0,
        "stockout_units": stockouts,
        "stockouts_avoided": stockouts_avoided,
        "waste_units": int(waste_units),
        "waste_reduced": waste_reduced,
        "units_transferred": int(transfer_units),
        "order_units_recommended": int(order_units),
        "pending_decisions": int(pending),
        "approved_decisions": int(approved),
        "rejected_decisions": int(rejected),
        "human_approval_rate": round(approved / accepted_total, 3) if accepted_total else None,
        "estimated_profit_saved": estimated_profit_saved,
        "without_agents": {
            "projected_stockouts": stockouts + stockouts_avoided,
            "projected_waste": int(waste_units + waste_reduced),
        },
        "with_agents": {
            "stockouts": stockouts,
            "waste": int(waste_units),
        },
    }


def _set_state(db: Session, key: str, value: str) -> None:
    db.execute(
        text("""
            INSERT INTO demo_state (key, value, updated_at)
            VALUES (:key, :value, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
        """),
        {"key": key, "value": value},
    )


def _get_state(db: Session, key: str, default: str | None = None) -> str | None:
    value = db.execute(text("SELECT value FROM demo_state WHERE key = :key"), {"key": key}).scalar()
    return value if value is not None else default


def _agent_roster() -> list[dict[str, str]]:
    return [
        {
            "name": "Inventory Watcher Agent",
            "role": "Checks customer demand against live ingredient stock.",
            "status": "watching",
        },
        {
            "name": "Demand Forecast Agent",
            "role": "Predicts rushes from day, scenario, and recent demand.",
            "status": "forecasting",
        },
        {
            "name": "Replenishment Agent",
            "role": "Proposes supplier orders before restaurants stock out.",
            "status": "planning",
        },
        {
            "name": "Transfer/Waste Agent",
            "role": "Moves excess food between nearby stores before it spoils.",
            "status": "optimizing",
        },
        {
            "name": "Manager Approval Agent",
            "role": "Keeps humans in control of final order and transfer actions.",
            "status": "awaiting decisions",
        },
    ]


def _emit_event(
    db: Session,
    tick_id: int | None,
    agent_name: str,
    event_type: str,
    severity: str,
    message: str,
    store_id: int | None = None,
    item_id: int | None = None,
) -> None:
    db.execute(
        text("""
            INSERT INTO agent_events
                (tick_id, agent_name, event_type, store_id, item_id, severity, message)
            VALUES (:tick_id, :agent, :event_type, :store_id, :item_id, :severity, :message)
        """),
        {
            "tick_id": tick_id,
            "agent": agent_name,
            "event_type": event_type,
            "store_id": store_id,
            "item_id": item_id,
            "severity": severity,
            "message": message,
        },
    )


def _avg_daily_demand(db: Session, store_id: int, item_id: int) -> float:
    value = db.execute(
        text("""
            SELECT COALESCE(AVG(quantity), 0)
            FROM (
                SELECT quantity
                FROM demand_history
                WHERE store_id = :store_id AND item_id = :item_id
                ORDER BY date DESC
                LIMIT 28
            ) recent
        """),
        {"store_id": store_id, "item_id": item_id},
    ).scalar()
    return float(value or 0)


def _inventory_qty(db: Session, store_id: int, item_id: int) -> int:
    value = db.execute(
        text("""
            SELECT COALESCE(SUM(quantity), 0)
            FROM inventory
            WHERE location_type = 'store' AND location_id = :store_id AND item_id = :item_id
        """),
        {"store_id": store_id, "item_id": item_id},
    ).scalar()
    return int(value or 0)


def _forecast_units(
    avg_daily: float,
    scenario: str,
    days: int = 3,
    demand_multiplier_adjustment: float = 1.0,
) -> int:
    multiplier = float(SCENARIOS.get(scenario, SCENARIOS[DEFAULT_SCENARIO])["demand_multiplier"])
    return max(1, int(round(avg_daily * days * multiplier * demand_multiplier_adjustment)))


def _live_demand_adjustment(db: Session) -> float:
    raw = db.execute(
        text("SELECT value FROM demo_state WHERE key = 'live_signal_summary'")
    ).scalar()
    if not raw:
        return 1.0
    try:
        return float(json.loads(raw).get("demand_multiplier", 1.0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return 1.0


def _expire_inventory(db: Session, tick_id: int, sim_date: date) -> None:
    rows = db.execute(
        text("""
            SELECT id, location_id, item_id, quantity
            FROM inventory
            WHERE location_type = 'store'
              AND quantity > 0
              AND expiry_date IS NOT NULL
              AND expiry_date < :sim_date
        """),
        {"sim_date": sim_date},
    ).fetchall()
    for row in rows:
        db.execute(text("UPDATE inventory SET quantity = 0 WHERE id = :id"), {"id": row.id})
        db.execute(
            text("""
                INSERT INTO waste_events (tick_id, store_id, item_id, quantity, reason)
                VALUES (:tick_id, :store_id, :item_id, :quantity, 'expired')
            """),
            {
                "tick_id": tick_id,
                "store_id": row.location_id,
                "item_id": row.item_id,
                "quantity": row.quantity,
            },
        )
        _emit_event(
            db,
            tick_id,
            "Transfer/Waste Agent",
            "waste_detected",
            "critical",
            f"{row.quantity} units expired before the agents could rescue them.",
            row.location_id,
            row.item_id,
        )


def _simulate_customer_demand(db: Session, tick_id: int, sim_day: int, multiplier: float) -> None:
    stores = db.execute(text("SELECT id, name FROM stores ORDER BY id LIMIT 8")).fetchall()
    items = db.execute(
        text("""
            SELECT id, name
            FROM items
            WHERE name ILIKE '%Chicken%' OR name ILIKE '%Bread%' OR name ILIKE '%Banana%'
               OR name ILIKE '%Sourdough%' OR name ILIKE '%Milk%'
            ORDER BY id
            LIMIT 5
        """)
    ).fetchall()
    if not items:
        items = db.execute(text("SELECT id, name FROM items ORDER BY id LIMIT 5")).fetchall()

    for store in stores:
        for item in items:
            avg = _avg_daily_demand(db, store.id, item.id)
            rng = random.Random(sim_day * 100000 + store.id * 100 + item.id)
            noise = rng.uniform(0.75, 1.25)
            demand = max(1, int(round(avg * multiplier * noise)))
            result = _consume_store_inventory(db, store.id, item.id, demand)
            db.execute(
                text("""
                    INSERT INTO simulated_demand_events
                        (tick_id, store_id, item_id, demand, fulfilled, stockout_units)
                    VALUES (:tick_id, :store_id, :item_id, :demand, :fulfilled, :stockout)
                """),
                {
                    "tick_id": tick_id,
                    "store_id": store.id,
                    "item_id": item.id,
                    "demand": result.demand,
                    "fulfilled": result.fulfilled,
                    "stockout": result.stockout_units,
                },
            )
            if result.stockout_units > 0:
                _emit_event(
                    db,
                    tick_id,
                    "Inventory Watcher Agent",
                    "stockout_risk",
                    "critical",
                    f"{store.name} could not fulfill {result.stockout_units} units of {item.name}; replenishment is required.",
                    store.id,
                    item.id,
                )


def _consume_store_inventory(db: Session, store_id: int, item_id: int, demand: int) -> DemandResult:
    remaining = demand
    fulfilled = 0
    rows = db.execute(
        text("""
            SELECT id, quantity
            FROM inventory
            WHERE location_type = 'store'
              AND location_id = :store_id
              AND item_id = :item_id
              AND quantity > 0
            ORDER BY expiry_date NULLS LAST, id
        """),
        {"store_id": store_id, "item_id": item_id},
    ).fetchall()
    for row in rows:
        if remaining <= 0:
            break
        taken = min(int(row.quantity), remaining)
        fulfilled += taken
        remaining -= taken
        db.execute(
            text("UPDATE inventory SET quantity = quantity - :taken WHERE id = :id"),
            {"taken": taken, "id": row.id},
        )
    return DemandResult(demand=demand, fulfilled=fulfilled, stockout_units=max(0, demand - fulfilled))


def _run_inventory_watcher(db: Session, tick_id: int) -> None:
    rows = db.execute(
        text("""
            SELECT s.id AS store_id, s.name AS store_name, i.id AS item_id, i.name AS item_name,
                   COALESCE(SUM(inv.quantity), 0) AS quantity
            FROM stores s
            CROSS JOIN items i
            LEFT JOIN inventory inv
              ON inv.location_type = 'store'
             AND inv.location_id = s.id
             AND inv.item_id = i.id
            WHERE i.id IN (SELECT id FROM items ORDER BY id LIMIT 6)
            GROUP BY s.id, s.name, i.id, i.name
            ORDER BY s.id, i.id
        """)
    ).fetchall()
    scenario = _get_state(db, "scenario", DEFAULT_SCENARIO)
    live_adjustment = _live_demand_adjustment(db)
    for row in rows:
        avg = _avg_daily_demand(db, row.store_id, row.item_id)
        target = _forecast_units(avg, scenario, 2, live_adjustment)
        if row.quantity < target:
            severity = "critical" if row.quantity < target * 0.45 else "warning"
            _emit_event(
                db,
                tick_id,
                "Inventory Watcher Agent",
                "low_stock",
                severity,
                f"{row.store_name} has {int(row.quantity)} units of {row.item_name}; two-day need is about {target}.",
                row.store_id,
                row.item_id,
            )


def _run_replenishment_agent(db: Session, tick_id: int, scenario: str) -> None:
    rows = db.execute(
        text("""
            SELECT s.id AS store_id, s.name AS store_name, i.id AS item_id, i.name AS item_name,
                   i.shelf_life_days, COALESCE(SUM(inv.quantity), 0) AS quantity
            FROM stores s
            CROSS JOIN items i
            LEFT JOIN inventory inv
              ON inv.location_type = 'store'
             AND inv.location_id = s.id
             AND inv.item_id = i.id
            WHERE i.id IN (SELECT id FROM items ORDER BY id LIMIT 6)
            GROUP BY s.id, s.name, i.id, i.name, i.shelf_life_days
            ORDER BY s.id, i.id
        """)
    ).fetchall()
    created = 0
    live_adjustment = _live_demand_adjustment(db)
    for row in rows:
        if created >= 8:
            return
        avg = _avg_daily_demand(db, row.store_id, row.item_id)
        forecast = _forecast_units(avg, scenario, 3, live_adjustment)
        safety = max(5, int(avg * 0.8))
        target = forecast + safety
        current_qty = int(row.quantity or 0)
        if current_qty >= target:
            continue
        sellable_cap = max(8, int(avg * min(row.shelf_life_days, 5)))
        qty = min(target - current_qty, sellable_cap)
        if qty < 8:
            continue
        key = f"demo-order-{tick_id}-{row.store_id}-{row.item_id}"
        reason = (
            f"{row.store_name} has {current_qty} units of {row.item_name}; "
            f"forecasted three-day need is {forecast} plus {safety} safety units."
        )
        impact = f"Prevents about {min(qty, target - current_qty)} shortage units before the next delivery window."
        if _create_decision(
            db,
            tick_id,
            "order",
            "Replenishment Agent",
            row.store_id,
            None,
            row.item_id,
            qty,
            key,
            reason,
            impact,
        ):
            created += 1
            _emit_event(
                db,
                tick_id,
                "Replenishment Agent",
                "order_recommended",
                "warning",
                f"Recommended ordering {qty} units of {row.item_name} for {row.store_name}.",
                row.store_id,
                row.item_id,
            )


def _run_transfer_agent(db: Session, tick_id: int, scenario: str) -> None:
    sources = db.execute(
        text("""
            SELECT inv.location_id AS store_id, s.name AS store_name, inv.item_id,
                   i.name AS item_name, SUM(inv.quantity) AS quantity,
                   MIN(inv.expiry_date) AS nearest_expiry
            FROM inventory inv
            JOIN stores s ON s.id = inv.location_id
            JOIN items i ON i.id = inv.item_id
            WHERE inv.location_type = 'store'
              AND inv.quantity > 0
              AND inv.expiry_date IS NOT NULL
              AND inv.expiry_date <= CURRENT_DATE + INTERVAL '3 days'
            GROUP BY inv.location_id, s.name, inv.item_id, i.name
            HAVING SUM(inv.quantity) >= 12
            ORDER BY nearest_expiry, quantity DESC
            LIMIT 8
        """)
    ).fetchall()
    created = 0
    live_adjustment = _live_demand_adjustment(db)
    for source in sources:
        avg_source = _avg_daily_demand(db, source.store_id, source.item_id)
        local_need = _forecast_units(avg_source, scenario, 2, live_adjustment)
        transferable = int(source.quantity) - local_need
        if transferable < 10:
            continue
        sink = _best_transfer_sink(db, source.store_id, source.item_id, scenario)
        if not sink:
            _create_markdown_decision(db, tick_id, source)
            continue
        qty = min(transferable, int(sink["shortfall"]))
        if qty < 10:
            continue
        transfer_cost = TRANSFER_FIXED_COST + sink["dist_km"] * TRANSFER_KM_COST + qty * TRANSFER_UNIT_COST
        waste_cost = qty * UNIT_VALUE
        if transfer_cost >= waste_cost:
            _create_markdown_decision(db, tick_id, source)
            continue
        key = f"demo-transfer-{tick_id}-{source.store_id}-{sink['store_id']}-{source.item_id}"
        reason = (
            f"{source.store_name} has {int(source.quantity)} units of {source.item_name} near expiry; "
            f"{sink['store_name']} is short by about {int(sink['shortfall'])} units and is {sink['dist_km']:.1f} km away."
        )
        impact = f"Rescues {qty} units from waste and avoids a supplier order at the receiving restaurant."
        if _create_decision(
            db,
            tick_id,
            "transfer",
            "Transfer/Waste Agent",
            source.store_id,
            sink["store_id"],
            source.item_id,
            qty,
            key,
            reason,
            impact,
        ):
            created += 1
            _emit_event(
                db,
                tick_id,
                "Transfer/Waste Agent",
                "transfer_recommended",
                "warning",
                f"Recommended moving {qty} units of {source.item_name} from {source.store_name} to {sink['store_name']}.",
                source.store_id,
                source.item_id,
            )
        if created >= 5:
            return


def _create_markdown_decision(db: Session, tick_id: int, source: Any) -> None:
    qty = min(int(source.quantity), 40)
    key = f"demo-markdown-{tick_id}-{source.store_id}-{source.item_id}"
    _create_decision(
        db,
        tick_id,
        "markdown",
        "Transfer/Waste Agent",
        source.store_id,
        None,
        source.item_id,
        qty,
        key,
        f"{source.store_name} has {int(source.quantity)} units of {source.item_name} near expiry and no economical nearby transfer.",
        f"Markdown can recover value on {qty} units before spoilage.",
    )


def _best_transfer_sink(db: Session, source_store_id: int, item_id: int, scenario: str) -> dict[str, Any] | None:
    source = db.execute(
        text("SELECT lat, lng FROM stores WHERE id = :id"),
        {"id": source_store_id},
    ).fetchone()
    candidates = db.execute(
        text("""
            SELECT s.id, s.name, s.lat, s.lng, COALESCE(SUM(inv.quantity), 0) AS quantity
            FROM stores s
            LEFT JOIN inventory inv
              ON inv.location_type = 'store'
             AND inv.location_id = s.id
             AND inv.item_id = :item_id
            WHERE s.id <> :source_store_id
            GROUP BY s.id, s.name, s.lat, s.lng
        """),
        {"source_store_id": source_store_id, "item_id": item_id},
    ).fetchall()
    ranked = []
    live_adjustment = _live_demand_adjustment(db)
    for candidate in candidates:
        dist = _haversine_km(source.lat, source.lng, candidate.lat, candidate.lng)
        if dist > 25:
            continue
        avg = _avg_daily_demand(db, candidate.id, item_id)
        need = _forecast_units(avg, scenario, 3, live_adjustment)
        shortfall = max(0, need - int(candidate.quantity or 0))
        if shortfall >= 10:
            ranked.append(
                {
                    "store_id": candidate.id,
                    "store_name": candidate.name,
                    "shortfall": shortfall,
                    "dist_km": dist,
                }
            )
    ranked.sort(key=lambda r: (-r["shortfall"], r["dist_km"]))
    return ranked[0] if ranked else None


def _create_decision(
    db: Session,
    tick_id: int,
    decision_type: str,
    agent_name: str,
    store_id: int,
    target_store_id: int | None,
    item_id: int,
    quantity: int,
    idempotency_key: str,
    reason: str,
    expected_impact: str,
) -> bool:
    exists = db.execute(
        text("SELECT id FROM agent_decisions WHERE idempotency_key = :key"),
        {"key": idempotency_key},
    ).scalar()
    if exists:
        return False
    db.execute(
        text("""
            INSERT INTO agent_decisions
                (tick_id, decision_type, agent_name, store_id, target_store_id, item_id,
                 quantity, idempotency_key, reason, expected_impact)
            VALUES
                (:tick_id, :decision_type, :agent_name, :store_id, :target_store_id, :item_id,
                 :quantity, :idempotency_key, :reason, :expected_impact)
        """),
        {
            "tick_id": tick_id,
            "decision_type": decision_type,
            "agent_name": agent_name,
            "store_id": store_id,
            "target_store_id": target_store_id,
            "item_id": item_id,
            "quantity": int(quantity),
            "idempotency_key": idempotency_key,
            "reason": reason,
            "expected_impact": expected_impact,
        },
    )
    return True


def _emit_manager_summary(db: Session, tick_id: int) -> None:
    pending = db.execute(
        text("SELECT COUNT(*) FROM agent_decisions WHERE status = 'pending'")
    ).scalar() or 0
    _emit_event(
        db,
        tick_id,
        "Manager Approval Agent",
        "approval_queue",
        "info",
        f"{pending} agent decisions are waiting for manager approval.",
    )


def _decide(db: Session, decision_id: int, action: str) -> dict[str, Any]:
    ensure_demo_schema(db)
    decision = db.execute(
        text("SELECT * FROM agent_decisions WHERE id = :id"),
        {"id": decision_id},
    ).fetchone()
    if not decision:
        raise ValueError(f"Decision {decision_id} not found")
    approval_key = f"demo-{action}-{decision_id}"
    existing = db.execute(
        text("SELECT id FROM approval_events WHERE idempotency_key = :key"),
        {"key": approval_key},
    ).scalar()
    if existing:
        return {"decision_id": decision_id, "status": decision.status, "idempotent": True}
    if decision.status != "pending":
        return {"decision_id": decision_id, "status": decision.status, "idempotent": True}

    db.execute(
        text("""
            UPDATE agent_decisions
            SET status = :action, decided_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """),
        {"action": action, "id": decision_id},
    )
    db.execute(
        text("""
            INSERT INTO approval_events (decision_id, action, idempotency_key)
            VALUES (:decision_id, :action, :key)
        """),
        {"decision_id": decision_id, "action": action, "key": approval_key},
    )
    if action == "approved":
        _apply_approved_decision(db, decision)
    _emit_event(
        db,
        decision.tick_id,
        "Manager Approval Agent",
        action,
        "info",
        f"Manager {action} {decision.decision_type} decision #{decision_id}.",
        decision.store_id,
        decision.item_id,
    )
    db.commit()
    return {"decision_id": decision_id, "status": action, "idempotent": False}


def _apply_approved_decision(db: Session, decision: Any) -> None:
    if decision.decision_type == "order":
        _apply_order(db, decision)
    elif decision.decision_type == "transfer":
        _apply_transfer(db, decision)
    elif decision.decision_type in {"markdown", "donation"}:
        _apply_markdown_or_donation(db, decision)


def _apply_order(db: Session, decision: Any) -> None:
    warehouse = db.execute(
        text("""
            SELECT location_id, quantity
            FROM inventory
            WHERE location_type = 'warehouse' AND item_id = :item_id AND quantity > 0
            ORDER BY quantity DESC
            LIMIT 1
        """),
        {"item_id": decision.item_id},
    ).fetchone()
    from_location_id = warehouse.location_id if warehouse else None
    if warehouse:
        removed = min(int(warehouse.quantity), int(decision.quantity))
        db.execute(
            text("""
                UPDATE inventory
                SET quantity = quantity - :qty
                WHERE location_type = 'warehouse'
                  AND location_id = :location_id
                  AND item_id = :item_id
                  AND quantity >= :qty
            """),
            {"qty": removed, "location_id": warehouse.location_id, "item_id": decision.item_id},
        )
    shelf = db.execute(
        text("SELECT shelf_life_days FROM items WHERE id = :item_id"),
        {"item_id": decision.item_id},
    ).scalar() or 7
    db.execute(
        text("""
            INSERT INTO inventory (location_id, location_type, item_id, quantity, expiry_date)
            VALUES (:store_id, 'store', :item_id, :qty, :expiry)
        """),
        {
            "store_id": decision.store_id,
            "item_id": decision.item_id,
            "qty": decision.quantity,
            "expiry": date.today() + timedelta(days=int(shelf)),
        },
    )
    _record_movement(
        db,
        decision.id,
        "warehouse_order",
        from_location_id,
        "warehouse" if from_location_id else None,
        decision.store_id,
        "store",
        decision.item_id,
        decision.quantity,
    )


def _apply_transfer(db: Session, decision: Any) -> None:
    moved = _move_fifo(
        db,
        decision.store_id,
        decision.target_store_id,
        decision.item_id,
        decision.quantity,
    )
    _record_movement(
        db,
        decision.id,
        "store_transfer",
        decision.store_id,
        "store",
        decision.target_store_id,
        "store",
        decision.item_id,
        moved,
    )


def _apply_markdown_or_donation(db: Session, decision: Any) -> None:
    removed = _remove_fifo(db, decision.store_id, decision.item_id, decision.quantity)
    _record_movement(
        db,
        decision.id,
        decision.decision_type,
        decision.store_id,
        "store",
        None,
        None,
        decision.item_id,
        removed,
    )


def _move_fifo(db: Session, from_store: int, to_store: int, item_id: int, quantity: int) -> int:
    remaining = quantity
    moved = 0
    rows = db.execute(
        text("""
            SELECT id, quantity, expiry_date
            FROM inventory
            WHERE location_type = 'store'
              AND location_id = :from_store
              AND item_id = :item_id
              AND quantity > 0
            ORDER BY expiry_date NULLS LAST, id
        """),
        {"from_store": from_store, "item_id": item_id},
    ).fetchall()
    for row in rows:
        if remaining <= 0:
            break
        take = min(int(row.quantity), remaining)
        moved += take
        remaining -= take
        db.execute(text("UPDATE inventory SET quantity = quantity - :take WHERE id = :id"), {"take": take, "id": row.id})
        db.execute(
            text("""
                INSERT INTO inventory (location_id, location_type, item_id, quantity, expiry_date)
                VALUES (:to_store, 'store', :item_id, :quantity, :expiry)
            """),
            {"to_store": to_store, "item_id": item_id, "quantity": take, "expiry": row.expiry_date},
        )
    return moved


def _remove_fifo(db: Session, store_id: int, item_id: int, quantity: int) -> int:
    remaining = quantity
    removed = 0
    rows = db.execute(
        text("""
            SELECT id, quantity
            FROM inventory
            WHERE location_type = 'store'
              AND location_id = :store_id
              AND item_id = :item_id
              AND quantity > 0
            ORDER BY expiry_date NULLS LAST, id
        """),
        {"store_id": store_id, "item_id": item_id},
    ).fetchall()
    for row in rows:
        if remaining <= 0:
            break
        take = min(int(row.quantity), remaining)
        removed += take
        remaining -= take
        db.execute(text("UPDATE inventory SET quantity = quantity - :take WHERE id = :id"), {"take": take, "id": row.id})
    return removed


def _record_movement(
    db: Session,
    decision_id: int,
    movement_type: str,
    from_location_id: int | None,
    from_location_type: str | None,
    to_location_id: int | None,
    to_location_type: str | None,
    item_id: int,
    quantity: int,
) -> None:
    db.execute(
        text("""
            INSERT INTO inventory_movements
                (decision_id, movement_type, from_location_id, from_location_type,
                 to_location_id, to_location_type, item_id, quantity)
            VALUES
                (:decision_id, :movement_type, :from_location_id, :from_location_type,
                 :to_location_id, :to_location_type, :item_id, :quantity)
        """),
        {
            "decision_id": decision_id,
            "movement_type": movement_type,
            "from_location_id": from_location_id,
            "from_location_type": from_location_type,
            "to_location_id": to_location_id,
            "to_location_type": to_location_type,
            "item_id": item_id,
            "quantity": quantity,
        },
    )


def _restaurants(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(text("SELECT id, name, lat, lng FROM stores ORDER BY id")).fetchall()
    scenario = _get_state(db, "scenario", DEFAULT_SCENARIO)
    live_adjustment = _live_demand_adjustment(db)
    restaurants = []
    for row in rows:
        item_rows = db.execute(
            text("""
                SELECT i.id, i.name, i.shelf_life_days,
                       COALESCE(SUM(inv.quantity), 0) AS quantity,
                       MIN(inv.expiry_date) AS nearest_expiry
                FROM items i
                LEFT JOIN inventory inv
                  ON inv.location_type = 'store'
                 AND inv.location_id = :store_id
                 AND inv.item_id = i.id
                GROUP BY i.id, i.name, i.shelf_life_days
                ORDER BY i.id
                LIMIT 6
            """),
            {"store_id": row.id},
        ).fetchall()
        top_items = []
        max_risk = 0.0
        expiry_risk = 0.0
        total_units = 0
        for item in item_rows:
            avg = _avg_daily_demand(db, row.id, item.id)
            need = _forecast_units(avg, scenario, 3, live_adjustment)
            qty = int(item.quantity or 0)
            risk = 1.0 if need <= 0 and qty == 0 else max(0.0, min(1.0, (need - qty) / max(need, 1)))
            if item.nearest_expiry:
                days_to_expiry = (item.nearest_expiry - date.today()).days
                item_expiry_risk = 1.0 if days_to_expiry <= 1 and qty > 0 else max(0.0, min(1.0, (4 - days_to_expiry) / 4))
            else:
                days_to_expiry = None
                item_expiry_risk = 0.0
            max_risk = max(max_risk, risk)
            expiry_risk = max(expiry_risk, item_expiry_risk)
            total_units += qty
            top_items.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "quantity": qty,
                    "forecast_3d": need,
                    "risk": round(risk, 2),
                    "days_to_expiry": days_to_expiry,
                }
            )
        status = "healthy"
        if expiry_risk >= 0.65:
            status = "expiry"
        if max_risk >= 0.65:
            status = "critical"
        elif max_risk >= 0.35:
            status = "low"
        restaurants.append(
            {
                "id": row.id,
                "name": row.name,
                "lat": row.lat,
                "lng": row.lng,
                "inventory_units": total_units,
                "stockout_risk": round(max_risk, 2),
                "expiry_risk": round(expiry_risk, 2),
                "status": status,
                "top_items": top_items,
            }
        )
    return restaurants


def _warehouses(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text("""
            SELECT w.id, w.name, w.lat, w.lng, COALESCE(SUM(inv.quantity), 0) AS inventory_units
            FROM warehouses w
            LEFT JOIN inventory inv ON inv.location_type = 'warehouse' AND inv.location_id = w.id
            GROUP BY w.id, w.name, w.lat, w.lng
            ORDER BY w.id
        """)
    ).fetchall()
    return [
        {
            "id": r.id,
            "name": r.name,
            "lat": r.lat,
            "lng": r.lng,
            "inventory_units": int(r.inventory_units or 0),
        }
        for r in rows
    ]


def _routes(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text("""
            SELECT d.id, d.decision_type, d.store_id, d.target_store_id, d.quantity,
                   s.lat AS from_lat, s.lng AS from_lng, s.name AS from_name,
                   ts.lat AS to_lat, ts.lng AS to_lng, ts.name AS to_name,
                   i.name AS item_name
            FROM agent_decisions d
            JOIN stores s ON s.id = d.store_id
            LEFT JOIN stores ts ON ts.id = d.target_store_id
            JOIN items i ON i.id = d.item_id
            WHERE d.status = 'pending'
              AND d.decision_type IN ('order', 'transfer')
            ORDER BY d.created_at DESC
            LIMIT 15
        """)
    ).fetchall()
    warehouses = _warehouses(db)
    default_wh = warehouses[0] if warehouses else None
    routes = []
    for r in rows:
        if r.decision_type == "transfer" and r.target_store_id:
            routes.append(
                {
                    "id": r.id,
                    "type": "transfer",
                    "from": {"lat": r.from_lat, "lng": r.from_lng, "name": r.from_name},
                    "to": {"lat": r.to_lat, "lng": r.to_lng, "name": r.to_name},
                    "quantity": r.quantity,
                    "item_name": r.item_name,
                }
            )
        elif r.decision_type == "order" and default_wh:
            routes.append(
                {
                    "id": r.id,
                    "type": "order",
                    "from": {"lat": default_wh["lat"], "lng": default_wh["lng"], "name": default_wh["name"]},
                    "to": {"lat": r.from_lat, "lng": r.from_lng, "name": r.from_name},
                    "quantity": r.quantity,
                    "item_name": r.item_name,
                }
            )
    return routes


def _decision_row(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "tick_id": row.tick_id,
        "decision_type": row.decision_type,
        "agent_name": row.agent_name,
        "store_id": row.store_id,
        "store_name": row.store_name,
        "target_store_id": row.target_store_id,
        "target_store_name": row.target_store_name,
        "item_id": row.item_id,
        "item_name": row.item_name,
        "quantity": row.quantity,
        "status": row.status,
        "reason": row.reason,
        "expected_impact": row.expected_impact,
        "idempotency_key": row.idempotency_key,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _prime_transfer_scenario(db: Session) -> None:
    stores = db.execute(text("SELECT id FROM stores ORDER BY id LIMIT 2")).fetchall()
    item_id = db.execute(
        text("SELECT id FROM items WHERE name ILIKE '%Chicken%' ORDER BY id LIMIT 1")
    ).scalar()
    if len(stores) < 2 or not item_id:
        return
    source_id = stores[0].id
    sink_id = stores[1].id
    db.execute(
        text("""
            INSERT INTO inventory (location_id, location_type, item_id, quantity, expiry_date)
            VALUES (:source_id, 'store', :item_id, 110, CURRENT_DATE + INTERVAL '1 day')
        """),
        {"source_id": source_id, "item_id": item_id},
    )
    db.execute(
        text("""
            UPDATE inventory
            SET quantity = GREATEST(quantity - 80, 0)
            WHERE location_type = 'store' AND location_id = :sink_id AND item_id = :item_id
        """),
        {"sink_id": sink_id, "item_id": item_id},
    )


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(a))
