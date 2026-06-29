"""
LangGraph multi-agent decision engine for StockFlow.

The graph is intentionally deterministic: LangGraph orchestrates specialized
agents and tool calls, while the tools perform auditable supply-chain logic.
LLMs can be layered on later for richer language, but the core decisions remain
testable and repeatable.
"""

from __future__ import annotations

from typing import Any, TypedDict

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - exercised only in incomplete envs
    END = "__end__"
    StateGraph = None

from agents import demo_simulator as sim


class DecisionGraphState(TypedDict, total=False):
    tick_id: int
    scenario: str
    risk_items: list[dict[str, Any]]
    forecast_items: list[dict[str, Any]]
    transfer_candidates: list[dict[str, Any]]
    baseline_score: dict[str, Any]
    decisions_before: int
    decisions_after: int
    trace_count: int


def run_langgraph_decision_engine(db: Session, tick_id: int, scenario: str) -> dict[str, Any]:
    """
    Run the agent graph for one simulation tick.

    Returns a summary that can be rendered in the UI or scored in tests.
    """
    if StateGraph is None:
        return _run_fallback_graph(db, tick_id, scenario)

    graph = StateGraph(DecisionGraphState)
    graph.add_node("inventory_watcher", lambda state: _inventory_watcher_agent(db, state))
    graph.add_node("demand_forecaster", lambda state: _demand_forecast_agent(db, state))
    graph.add_node("transfer_waste", lambda state: _transfer_waste_agent(db, state))
    graph.add_node("replenishment", lambda state: _replenishment_agent(db, state))
    graph.add_node("baseline_scorer", lambda state: _baseline_scorer_agent(db, state))

    graph.set_entry_point("inventory_watcher")
    graph.add_edge("inventory_watcher", "demand_forecaster")
    graph.add_edge("demand_forecaster", "transfer_waste")
    graph.add_edge("transfer_waste", "replenishment")
    graph.add_edge("replenishment", "baseline_scorer")
    graph.add_edge("baseline_scorer", END)

    app = graph.compile()
    initial_state: DecisionGraphState = {
        "tick_id": tick_id,
        "scenario": scenario,
        "decisions_before": _decision_count(db),
        "trace_count": 0,
    }
    return dict(app.invoke(initial_state))


def _run_fallback_graph(db: Session, tick_id: int, scenario: str) -> dict[str, Any]:
    """Keep local development usable if LangGraph is not installed yet."""
    state: DecisionGraphState = {"tick_id": tick_id, "scenario": scenario, "decisions_before": _decision_count(db)}
    state.update(_inventory_watcher_agent(db, state))
    state.update(_demand_forecast_agent(db, state))
    state.update(_transfer_waste_agent(db, state))
    state.update(_replenishment_agent(db, state))
    state.update(_baseline_scorer_agent(db, state))
    return dict(state)


def _inventory_watcher_agent(db: Session, state: DecisionGraphState) -> DecisionGraphState:
    risk_items = tool_scan_inventory_risk(db, state["scenario"], limit=12)
    critical = sum(1 for item in risk_items if item["risk"] >= 0.65)
    _trace(
        db,
        state["tick_id"],
        "Inventory Watcher Agent",
        "scan_inventory_risk",
        f"scenario={state['scenario']}, limit=12",
        f"Found {len(risk_items)} risk items; {critical} are critical.",
        "Pass risky store-item pairs to the forecasting agent.",
    )
    sim._emit_event(
        db,
        state["tick_id"],
        "Inventory Watcher Agent",
        "tool_call",
        "info",
        f"Called scan_inventory_risk and found {len(risk_items)} store-item risks.",
    )
    return {"risk_items": risk_items, "trace_count": int(state.get("trace_count", 0)) + 1}


def _demand_forecast_agent(db: Session, state: DecisionGraphState) -> DecisionGraphState:
    risk_items = state.get("risk_items", [])
    forecast_items = tool_forecast_risk_items(db, risk_items, state["scenario"])
    total_forecast = sum(item["forecast_3d"] for item in forecast_items)
    live_adjustment = sim._live_demand_adjustment(db)
    _trace(
        db,
        state["tick_id"],
        "Demand Forecast Agent",
        "forecast_risk_items",
        f"{len(risk_items)} risk items under {state['scenario']} with live_multiplier={live_adjustment:.2f}",
        f"Projected {total_forecast} units of three-day demand across risk items using free live signals.",
        "Pass forecasted shortages to transfer and replenishment agents.",
    )
    sim._emit_event(
        db,
        state["tick_id"],
        "Demand Forecast Agent",
        "tool_call",
        "info",
        f"Called forecast_risk_items for {len(risk_items)} risky store-item pairs.",
    )
    return {"forecast_items": forecast_items, "trace_count": int(state.get("trace_count", 0)) + 1}


def _transfer_waste_agent(db: Session, state: DecisionGraphState) -> DecisionGraphState:
    before = _decision_count(db)
    candidates = tool_find_transfer_candidates(db, state["scenario"])
    sim._run_transfer_agent(db, state["tick_id"], state["scenario"])
    created = _decision_count(db) - before
    _trace(
        db,
        state["tick_id"],
        "Transfer/Waste Agent",
        "find_transfer_candidates",
        f"scenario={state['scenario']}",
        f"Found {len(candidates)} transfer candidates and created {created} auditable decisions.",
        "Prefer transfer or markdown before creating new supplier demand.",
    )
    sim._emit_event(
        db,
        state["tick_id"],
        "Transfer/Waste Agent",
        "tool_call",
        "info",
        f"Called find_transfer_candidates before proposing {created} transfer/waste decisions.",
    )
    return {"transfer_candidates": candidates, "trace_count": int(state.get("trace_count", 0)) + 1}


def _replenishment_agent(db: Session, state: DecisionGraphState) -> DecisionGraphState:
    before = _decision_count(db)
    summary = tool_estimate_replenishment_need(db, state.get("forecast_items", []))
    sim._run_replenishment_agent(db, state["tick_id"], state["scenario"])
    created = _decision_count(db) - before
    _trace(
        db,
        state["tick_id"],
        "Replenishment Agent",
        "estimate_replenishment_need",
        f"{len(state.get('forecast_items', []))} forecasted risk items",
        f"Estimated {summary['shortage_units']} shortage units and created {created} order decisions.",
        "Draft supplier orders only for shortages not already handled by transfers.",
    )
    sim._emit_event(
        db,
        state["tick_id"],
        "Replenishment Agent",
        "tool_call",
        "info",
        f"Called estimate_replenishment_need before proposing {created} supplier orders.",
    )
    return {"decisions_after": _decision_count(db), "trace_count": int(state.get("trace_count", 0)) + 1}


def _baseline_scorer_agent(db: Session, state: DecisionGraphState) -> DecisionGraphState:
    baseline_score = tool_score_against_baseline(db)
    _trace(
        db,
        state["tick_id"],
        "Baseline Scorer Agent",
        "score_against_baseline",
        "current demand, waste, transfer, and approval events",
        (
            f"Baseline projected {baseline_score['without_agents']['projected_stockouts']} stockout units "
            f"and {baseline_score['without_agents']['projected_waste']} waste units."
        ),
        "Expose before/after proof metrics for the resume project.",
    )
    sim._emit_event(
        db,
        state["tick_id"],
        "Baseline Scorer Agent",
        "baseline_score",
        "info",
        "Scored agent decisions against the no-agent baseline.",
    )
    return {"baseline_score": baseline_score, "trace_count": int(state.get("trace_count", 0)) + 1}


def tool_scan_inventory_risk(db: Session, scenario: str, limit: int = 12) -> list[dict[str, Any]]:
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
        """)
    ).fetchall()
    risk_items = []
    live_adjustment = sim._live_demand_adjustment(db)
    for row in rows:
        avg = sim._avg_daily_demand(db, row.store_id, row.item_id)
        forecast = sim._forecast_units(avg, scenario, 3, live_adjustment)
        qty = int(row.quantity or 0)
        risk = max(0.0, min(1.0, (forecast - qty) / max(forecast, 1)))
        if risk >= 0.25:
            risk_items.append(
                {
                    "store_id": row.store_id,
                    "store_name": row.store_name,
                    "item_id": row.item_id,
                    "item_name": row.item_name,
                    "quantity": qty,
                    "forecast_3d": forecast,
                    "risk": round(risk, 3),
                }
            )
    risk_items.sort(key=lambda item: (-item["risk"], item["quantity"]))
    return risk_items[:limit]


def tool_forecast_risk_items(
    db: Session,
    risk_items: list[dict[str, Any]],
    scenario: str,
) -> list[dict[str, Any]]:
    forecasted = []
    live_adjustment = sim._live_demand_adjustment(db)
    for item in risk_items:
        avg = sim._avg_daily_demand(db, item["store_id"], item["item_id"])
        forecast = sim._forecast_units(avg, scenario, 3, live_adjustment)
        shortage = max(0, forecast - item["quantity"])
        forecasted.append({**item, "avg_daily_demand": round(avg, 1), "forecast_3d": forecast, "shortage": shortage})
    return forecasted


def tool_find_transfer_candidates(db: Session, scenario: str) -> list[dict[str, Any]]:
    rows = db.execute(
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
    candidates = []
    for row in rows:
        sink = sim._best_transfer_sink(db, row.store_id, row.item_id, scenario)
        if sink:
            candidates.append(
                {
                    "from_store_id": row.store_id,
                    "from_store_name": row.store_name,
                    "to_store_id": sink["store_id"],
                    "to_store_name": sink["store_name"],
                    "item_id": row.item_id,
                    "item_name": row.item_name,
                    "available_units": int(row.quantity),
                    "shortfall": int(sink["shortfall"]),
                    "distance_km": round(sink["dist_km"], 1),
                }
            )
    return candidates


def tool_estimate_replenishment_need(
    db: Session,
    forecast_items: list[dict[str, Any]],
) -> dict[str, Any]:
    shortage_units = sum(int(item.get("shortage", 0)) for item in forecast_items)
    critical_items = sum(1 for item in forecast_items if item.get("risk", 0) >= 0.65)
    pending_transfers = db.execute(
        text("""
            SELECT COALESCE(SUM(quantity), 0)
            FROM agent_decisions
            WHERE status = 'pending' AND decision_type = 'transfer'
        """)
    ).scalar() or 0
    return {
        "shortage_units": max(0, shortage_units - int(pending_transfers)),
        "critical_items": critical_items,
        "pending_transfer_units": int(pending_transfers),
    }


def tool_score_against_baseline(db: Session) -> dict[str, Any]:
    return sim.demo_impact_metrics(db)


def _trace(
    db: Session,
    tick_id: int,
    agent_name: str,
    tool_name: str,
    input_summary: str,
    observation: str,
    decision: str,
) -> None:
    db.execute(
        text("""
            INSERT INTO agent_reasoning_traces
                (tick_id, agent_name, tool_name, input_summary, observation, decision)
            VALUES
                (:tick_id, :agent_name, :tool_name, :input_summary, :observation, :decision)
        """),
        {
            "tick_id": tick_id,
            "agent_name": agent_name,
            "tool_name": tool_name,
            "input_summary": input_summary,
            "observation": observation,
            "decision": decision,
        },
    )


def _decision_count(db: Session) -> int:
    return int(db.execute(text("SELECT COUNT(*) FROM agent_decisions")).scalar() or 0)
