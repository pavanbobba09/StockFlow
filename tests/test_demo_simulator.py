import os

import pytest

from agents import demo_simulator as sim
from agents import langgraph_decision_engine as graph_engine
from agents import live_signals


def test_forecast_units_uses_scenario_multiplier():
    normal = sim._forecast_units(20, "delivery-delay", days=3)
    rush = sim._forecast_units(20, "game-day-spike", days=3)

    assert normal > 0
    assert rush > normal


def test_agent_roster_explains_agent_roles():
    names = {agent["name"] for agent in sim._agent_roster()}

    assert "Inventory Watcher Agent" in names
    assert "Demand Forecast Agent" in names
    assert "Replenishment Agent" in names
    assert "Transfer/Waste Agent" in names
    assert "Manager Approval Agent" in names


def test_langgraph_engine_exposes_supply_chain_tools():
    tool_names = {
        "tool_scan_inventory_risk",
        "tool_forecast_risk_items",
        "tool_find_transfer_candidates",
        "tool_estimate_replenishment_need",
        "tool_score_against_baseline",
    }

    assert tool_names <= set(dir(graph_engine))


def test_haversine_distance_is_reasonable_for_nearby_coordinates():
    distance = sim._haversine_km(33.7490, -84.3880, 33.7550, -84.3900)

    assert 0.5 <= distance <= 1.0


def test_live_weather_multiplier_raises_pressure_for_bad_weather():
    normal = live_signals._weather_multiplier("Clear", 0, 72, "5 mph")
    storm = live_signals._weather_multiplier("Thunderstorms and rain", 80, 91, "25 mph")

    assert normal == 1.0
    assert storm > normal


def test_default_live_signal_summary_is_neutral():
    summary = live_signals._default_summary("disabled")

    assert summary["demand_multiplier"] == 1.0
    assert "National Weather Service" in summary["providers"]


@pytest.mark.skipif(
    os.getenv("RUN_STOCKFLOW_DB_TESTS") != "1",
    reason="Set RUN_STOCKFLOW_DB_TESTS=1 to run Postgres-backed demo simulator tests.",
)
def test_demo_tick_creates_agent_decisions_from_seeded_database():
    from data.db import SessionLocal

    db = SessionLocal()
    try:
        sim.reset_demo(db)
        state = sim.run_demo_tick(db)

        assert state["sim_day"] == 1
        assert state["events"]
        assert state["metrics"]["total_demand"] > 0
        assert state["pending_decisions"]
    finally:
        db.close()


@pytest.mark.skipif(
    os.getenv("RUN_STOCKFLOW_DB_TESTS") != "1",
    reason="Set RUN_STOCKFLOW_DB_TESTS=1 to run Postgres-backed demo simulator tests.",
)
def test_approval_is_idempotent_for_pending_demo_decision():
    from data.db import SessionLocal

    db = SessionLocal()
    try:
        sim.reset_demo(db)
        state = sim.run_demo_tick(db)
        decision_id = state["pending_decisions"][0]["id"]

        first = sim.approve_decision(db, decision_id)
        second = sim.approve_decision(db, decision_id)

        assert first["status"] == "approved"
        assert second["status"] == "approved"
        assert second["idempotent"] is True
    finally:
        db.close()
