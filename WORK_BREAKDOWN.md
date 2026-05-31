# StockFlow Work Breakdown

Phased plan. Each phase ends with something that works end to end. Do not skip
ahead. Thin slice first, then widen.

## Phase 0: Setup

- Repo, Docker Compose for Postgres+PostGIS.
- Project skeleton: forecasting/, agents/, api/, data/, evals/.
- Drop CLAUDE.md and docs/ in. Confirm a fresh session can read context.

Done when: containers come up, schema migrations run, repo structure is in place.

## Phase 1: Data foundation

- Postgres + PostGIS schema: stores(lat,lng), warehouses(lat,lng),
  items(shelf_life_days), inventory, delivery_schedules, demand_history, orders.
- Synthetic data generator:
  - N stores with coordinates, a few warehouses.
  - items with shelf lives.
  - demand history per store-item: base level + weekly seasonality + noise +
    occasional event spikes. Make stores differ from each other.
  - delivery schedules per store (e.g. Tue/Fri).
- Set the current inventory state as rows in Postgres.

Done when: you can query "stock at store X", "demand history for store X item Y",
and "stores within R km of store X" (PostGIS).

## Phase 2: Forecasting + eval harness (evals first)

- Baseline forecaster per store-item. Start simple (moving average / seasonal
  naive), then improve. Simple baseline is fine and is your comparison point.
- Backtest harness: replay demand day by day, no future peeking. Plug in the
  forecaster, record predictions vs actuals.
- Metrics module: stockout rate, waste rate, fill rate, delivery cost,
  forecast accuracy (WAPE/MAPE), action acceptance rate (later).

Done when: you can run a backtest and get a scorecard. This is the spine of the
whole project. Everything later is judged on this.

## Phase 3: Replenishment agent, one store-item

- LangGraph graph for a single replenishment decision.
- Tools: get_stock, get_forecast, get_par_levels, draft_order (idempotent).
- Logic: order up to par adjusted for forecast to next delivery + safety stock,
  capped by shelf life.
- Propose-then-approve loop: agent drafts, you approve, place_order commits with
  the same idempotency key.
- Run it inside the backtest: does it cut stockouts/waste vs a dumb fixed-order
  baseline?

Done when: one store-item runs the full loop and beats the baseline on the
scorecard.

## Phase 4: Widen + transfer/waste agent

- Run replenishment across all store-items.
- Transfer/Waste agent: tools nearby_stores (PostGIS), suggest_transfer
  (idempotent), flag_expiry. Compares transfer vs waste vs reorder cost.
- Add transfers and markdowns into the backtest scoring.

Done when: cross-location transfers measurably reduce waste without raising
stockouts on the scorecard.

## Phase 5: Anomaly-explainer + API + observability

- Anomaly-explainer agent: explains forecast jumps and chronic stockouts.
- FastAPI: endpoints for proposals (approve/reject), state, metrics, explanations.
- Prometheus metrics + Grafana dashboard for the metric set.

Done when: you can hit the API, see proposals, approve them, and watch metrics
move in Grafana.

## Phase 6: 3D / geo frontend (last)

- Map of franchises + warehouses. Inventory level as color/height.
- Overlay proposed transfers as arrows between stores. Highlight near-expiry and
  stockout-risk stores.
- Reads the same state as everything else. No business logic in the frontend.

Done when: a human can glance at the map and understand where the system wants to
order, transfer, or mark down, and approve from there.

## Phase 7: Go live (do this LAST, after the full build works)

Goal: a public link a recruiter can click and watch the system run. Live link on
the resume beats a repo they never open. Build everything else first; this is the
final step.

What "live" means here: synthetic data, but the system runs continuously on a
hosted box. A clock ticks through simulated days, agents propose, you approve,
the map and Grafana update. It IS live, the data is just generated. That is fine
for a portfolio and nobody serious docks you for it if the engineering is real.

Steps:
- Make it run on a schedule. APScheduler or a simple loop advances a simulated
  clock. Each tick: generate demand, wake agents, write proposals to a Postgres
  queue table, update state. APScheduler runs in-process, so no broker, no Redis.
- Containerize the whole stack (already on Docker Compose from Phase 0). Make sure
  Postgres+PostGIS, API, scheduler, frontend all come up together.
- Host it. Cheap options: Railway, Render, Fly.io, or a small VPS. Pick one that
  runs containers and a managed Postgres so you are not babysitting it.
- Cost control: an always-on app costs a little and must run 24/7. If you do not
  want it always up, host it but let it idle, and add a "Start simulation" button
  that spins the clock on demand. Cheaper, still demoable instantly.
- Optional real-data spice: feed the forecaster one or two genuinely live external
  signals (weather API, public event calendars). Core inventory stays synthetic
  because there is no real source, but a real feed makes it feel less canned.
- Polish the landing state: when someone opens the link cold, they should
  immediately see the map populated and either a running sim or an obvious start
  button. No empty screen, no setup steps.

Done when: a stranger clicks the link and within seconds sees a living system,
agents proposing, metrics moving, the map showing stock and transfers.

Hosting decision (fill in when you get here):
- Platform: ____
- Always-on or sleep-with-start-button: ____
- Managed Postgres or self-hosted: ____
- Real external feed added (y/n): ____

## Stretch (only after the above works)

- Delivery route optimization (OR-Tools) warehouse-to-store.
- Warehouse placement / coverage analysis.
- Real or public dataset swapped in for synthetic.
- Multi-objective tuning (explicit weights on the metric set).

## Resume framing (for job applications later)

Backend + agentic + geospatial + forecasting on a real business problem. Lead
with the problem solved (balancing stockouts against waste across many locations)
and the evals-first backtest that proves it, not the tech list. Keep it concrete.
