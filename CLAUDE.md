# CLAUDE.md

This file gives any Claude session the full context for this project. Read it first.

## What this project is

StockFlow is a multi-agent system that keeps a food chain's locations stocked
without overstocking. It forecasts demand per location, drafts replenishment
orders, and coordinates inventory transfers between nearby stores using
geospatial reasoning. The goal is to cut stockouts and food waste at the same time.

One-line pitch: a multi-agent inventory brain for a food chain that balances
stockouts against waste across many locations and a few warehouses.

## The problem in five constraints

Every design choice traces back to one of these.

1. Demand is uncertain. Each location behaves differently (stadium nearby,
   weather, holidays, promotions).
2. Food is perishable. You cannot overstock to be safe. Time is a hard constraint.
3. Lead time and delivery schedules are fixed. You order before you know real demand.
4. Locations are not independent. Store A overstocked, Store B nearby short.
   A transfer may beat both reordering and waste.
5. Multiple competing objectives at once: minimize stockouts, minimize waste,
   minimize delivery cost, hold service level high. They trade off.

## Why agents (not just a model or dashboard)

A human GM reasons about one store well. They cannot reason about hundreds of
stores plus warehouses plus routes plus expiry clocks at once. Agents do the
breadth: per-location reasoning at scale, propose actions, surface the few
high-stakes decisions a human should approve. Human keeps judgment, system does scale.

## Core design principles (carry these into every decision)

- Evals first. Define how success is measured before building features.
- Small composable agents. One job each. No mega-agent.
- Idempotent tool calls. Placing an order twice on a retry is real money lost.
  Every tool that mutates state takes an idempotency key.
- Thin end-to-end slice first. One store, one item, full loop working, then widen.
- Human in the loop on mutations. Agents propose, humans approve order/transfer.

## Architecture (high level)

- Forecasting service: time-series models per store-item. NOT an agent.
- Data layer: Postgres + PostGIS. Holds everything: geo, inventory state,
  orders, and idempotency keys (no Redis, no cache, keep the stack lean).
- Agents (LangGraph):
  - Replenishment agent: reads stock + forecast + par levels + delivery schedule,
    drafts an order. Proposes, human approves.
  - Transfer/waste agent: watches expiry + cross-location stock, suggests
    transfers between nearby stores or markdowns before spoilage. Uses PostGIS.
  - Anomaly-explainer agent: explains forecast jumps and flags them to humans.
- Orchestration: LangGraph. API: FastAPI. Observability: Prometheus + Grafana.
- Frontend: 3D/geo map of franchises + warehouses, inventory as color/height.
  This is decision-support and monitoring, NOT the brains. Build logic first.

## Tools the agents call (all idempotent where they mutate)

- get_stock(store_id) -> current inventory
- get_forecast(store_id, item_id, horizon) -> predicted demand
- get_par_levels(store_id) -> target stock
- draft_order(store_id, items, idempotency_key) -> proposed order (no commit)
- place_order(order_id, idempotency_key) -> commits after human approve
- suggest_transfer(from_store, to_store, item, qty, idempotency_key)
- flag_expiry(store_id, item_id, expiry_date) -> markdown/donation trigger
- nearby_stores(store_id, radius) -> PostGIS spatial query

## What success looks like (metrics, evals-first)

Track and optimize together, not one in isolation:
- Stockout rate (lower better)
- Waste rate / spoilage value (lower better)
- Service level / fill rate (higher better)
- Delivery cost (lower better)
- Forecast accuracy (MAPE or WAPE per store-item)
- Agent action acceptance rate by humans (proxy for trust/quality)

Build a backtest harness: replay historical (or synthetic) demand, let the system
decide, score against the metrics above. That harness IS the eval suite.

## Data (start synthetic)

No real chain data needed to start. Generate synthetic:
- stores (with lat/lng), warehouses (with lat/lng), items (with shelf life)
- demand history per store-item with seasonality + noise + event spikes
- delivery schedules per store
This lets the whole thing be built solo. Swap in real/public data later.

## Build order (thin slice first)

See docs/WORK_BREAKDOWN.md for the full phased plan. Short version:
1. Synthetic data generator + Postgres/PostGIS schema.
2. Forecasting baseline (even a simple model) + backtest harness + metrics.
3. Replenishment agent for ONE store-item, full propose->approve loop.
4. Widen to many store-items. Add transfer/waste agent with PostGIS.
5. Anomaly-explainer agent. FastAPI surface. Observability.
6. 3D/geo frontend reading the same state.

## Tech stack

Python, FastAPI, LangGraph, LangChain, Postgres + PostGIS, Docker,
Prometheus/Grafana. Frontend map: keep simple (e.g. deck.gl or similar) and last.

## Hard rules / gotchas

- Mutating tools MUST be idempotent (idempotency key required).
- Agents never auto-commit orders or transfers. Human approves.
- The map is the last thing built, not the first. Logic before visuals.
- Keep agents small and single-purpose.
- Optimize the metric SET, never a single metric. Stockout vs waste trade off.
