# StockFlow Architecture

This explains how the pieces fit and why. Pair it with CLAUDE.md.

## The shape of the problem

Uncertain demand, perishable goods, fixed lead times, connected locations,
competing objectives. The system is always fighting two enemies at once:
stockouts and waste. They pull opposite ways. The win is the narrow middle path,
held across many locations that each behave differently.

## Layered view

```
+-------------------------------------------------------------+
|  Frontend (LAST to build)                                   |
|  3D/geo map: franchises + warehouses, inventory as          |
|  color/height. Monitoring + decision support only.          |
+-------------------------------------------------------------+
                          | reads same state
                          v
+-------------------------------------------------------------+
|  API layer: FastAPI                                         |
|  - exposes agent proposals for human approve/reject          |
|  - exposes state, metrics, explanations                      |
+-------------------------------------------------------------+
                          |
                          v
+-------------------------------------------------------------+
|  Agent layer: LangGraph orchestration                       |
|                                                             |
|  [Replenishment agent]  [Transfer/Waste agent]  [Anomaly    |
|   per store/cluster      cross-location +         explainer] |
|   drafts orders          PostGIS, expiry          flags +    |
|                          aware                    reasons    |
|                                                             |
|  All mutating tools idempotent. Agents PROPOSE only.         |
+-------------------------------------------------------------+
        |                       |                    |
        v                       v                    v
+-------------------------------------------------------------+
|  Forecasting service (NOT an agent)                         |
|  time-series per store-item. demand predictions.            |
+-------------------------------------------------------------+
                          |
                          v
+-------------------------------------------------------------+
|  Data layer                                                 |
|  Postgres + PostGIS: stores, warehouses, items, orders,     |
|    delivery schedules, demand history, inventory state, geo  |
|    coordinates, and idempotency keys. One store of truth.    |
+-------------------------------------------------------------+
                          |
                          v
+-------------------------------------------------------------+
|  Observability: Prometheus + Grafana                        |
|  metrics: stockout rate, waste, fill rate, delivery cost,    |
|  forecast accuracy, action acceptance rate                   |
+-------------------------------------------------------------+
```

## Why this split

Forecasting is a math problem, not an agent problem, so it lives in its own
service. Agents sit on top and reason: should I order, transfer, or flag? The
data layer holds truth. Postgres holds everything, including the inventory state
and the idempotency keys that stop double-orders on retries. No separate cache or
Redis: at this scale Postgres handles the hot path fine, and one fewer service
means one fewer thing to deploy and break. The map reads the same state
everything else does, so it never becomes a separate source of truth.

## The agents, in detail

### Replenishment agent
Trigger: scheduled (e.g. before each delivery cutoff) per store.
Reads: current stock, forecast for the horizon to next delivery, par levels,
delivery schedule, lead time.
Logic: how much will run out before the next truck? Order up to par, adjusted
for forecast and safety stock. Account for shelf life so it does not over-order
perishables.
Output: a drafted order. Never commits. Human approves -> place_order with the
same idempotency key.
This is basically the InboxOps propose-then-confirm pattern applied to stock.

### Transfer/Waste agent
Trigger: scheduled or on expiry-threshold events.
Reads: expiry dates, cross-location stock, store coordinates.
Logic: item near expiry at store A, short at nearby store B? Use PostGIS
nearby_stores to find candidates, compare transfer cost vs waste cost vs reorder
cost. Suggest the cheapest. If nothing nearby needs it, suggest markdown/donation.
Output: suggested transfer or markdown. Human approves.

### Anomaly-explainer agent
Trigger: forecast deltas beyond a threshold, or stockout/waste events.
Reads: forecast inputs, recent history, events/weather.
Logic: explain why a forecast jumped or why a store keeps stocking out.
Output: plain-language explanation surfaced to the human. No mutations.

## Idempotency (the rule that saves money)

Every mutating tool takes an idempotency key. The key is stored in Postgres with
a unique constraint. If the
same key arrives again (retry, network blip, double-click), the tool returns the
original result instead of acting twice. Ordering 400 tomatoes twice is real loss.
This is non-negotiable and applies to place_order, suggest_transfer commits, and
flag_expiry actions.

## The geospatial / 3D piece, scoped honestly

Where geography genuinely matters:
- Transfers: nearby_stores via PostGIS decides if a transfer beats waste.
- Delivery routing: warehouse-to-store, an OR-tools style optimization, not an LLM.
- Warehouse placement / coverage: where to put the next DC to cut delivery time.

The 3D map itself is monitoring and decision support. It makes the system legible
to a human (see overstocked vs short stores at a glance), but it is not where
decisions are computed. Build it last.

## Evals-first, concretely

Build a backtest harness early. Replay historical or synthetic demand day by day.
At each step the system makes decisions with only the information it would have
had at that time (no peeking at future demand). Score the run against the metric
set. Every change to an agent or the forecaster gets re-scored on the same
harness. That harness is the eval suite and the thing that proves the project
works, not a flashy demo.

## Failure modes to watch

- Optimizing one metric and quietly wrecking another (cut waste to zero by
  understocking -> stockouts explode). Always score the set together.
- Non-idempotent retries causing double orders.
- Agent auto-committing without human approval.
- Forecast peeking at the future in the backtest (data leakage), which makes
  results look great and mean nothing.
- Building the map before the logic.
