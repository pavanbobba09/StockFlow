# StockFlow

Multi-Agent Franchise Supply Chain Simulator.

StockFlow is an AI-agent portfolio project for restaurant franchise supply chains. It simulates a fictional quick-service restaurant network where customer demand drains store inventory, food expires, and agents coordinate replenishment orders and nearby store-to-store transfers before stockouts and waste happen.

The goal is not to be a generic full-stack dashboard or a visual-only demo. The live map is the recruiter-facing way to understand the system, but the project is built around the harder engineering work: deterministic agent decision logic, idempotent approval flows, geospatial store-to-store transfers, perishable inventory constraints, backtest metrics, and measurable stockout/waste tradeoffs.

One-line pitch: AI agents that help franchise restaurants order the right amount of food, rescue excess stock through nearby transfers, and reduce both stockouts and spoilage.

## Demo Agents

- **Inventory Watcher Agent** checks customer demand against live restaurant stock.
- **Demand Forecast Agent** predicts rushes from scenario, weekday/weekend patterns, and recent demand.
- **Replenishment Agent** proposes supplier orders when inventory will not cover near-term demand.
- **Transfer/Waste Agent** finds nearby restaurants that can use excess or expiring food before it spoils.
- **Manager Approval Agent** keeps humans in the loop for order, transfer, markdown, and donation decisions.

The demo path works without an LLM API key. Core decisions are deterministic and testable; LLM explanations can be added later as an enhancement.

## Why This Is Resume-Strong

StockFlow is designed to show backend, AI-agent, forecasting, and systems judgment in one project:

- **Agentic reasoning with business constraints**: agents do not just chat; they inspect stock, forecast demand, reason about shelf life, compare transfer cost versus waste cost, and propose actions.
- **Human-in-the-loop safety**: agents propose orders, transfers, markdowns, and donations; humans approve or reject mutations.
- **Idempotent mutation design**: decision approvals use idempotency keys so retries cannot duplicate real business actions.
- **Geospatial optimization**: transfer decisions use nearby-store reasoning so excess inventory can move before new supplier orders are placed.
- **Evals-first proof**: the backtest harness compares baseline forecasting and agent strategies using stockout rate, fill rate, waste rate, forecast error, and transfer impact.
- **Production-shaped API and persistence**: agent events, decisions, inventory movements, demand events, approval events, and waste events are stored durably.
- **LangGraph orchestration and reasoning traces**: the decision path runs through named graph nodes, calls explicit tools, and stores tool inputs, observations, and decisions.
- **Free live signals**: National Weather Service weather and Nager.Date holiday data can raise demand pressure without paid API keys.
- **Recruiter-readable visualization**: the frontend is not the product by itself; it is the live operations room that makes the agent system legible.

## LangGraph Decision Flow

Each simulation tick runs this multi-agent graph:

```text
Inventory Watcher Agent
  -> Demand Forecast Agent
  -> Transfer/Waste Agent
  -> Replenishment Agent
  -> Baseline Scorer Agent
```

The graph calls supply-chain tools such as:

- `scan_inventory_risk`
- `forecast_risk_items`
- `find_transfer_candidates`
- `estimate_replenishment_need`
- `score_against_baseline`

Every tool call writes a reasoning trace with agent name, tool name, input summary, observation, and decision. This makes the project visibly agentic without letting an LLM hallucinate business-critical inventory actions.

## Quick Start

### 1. Create virtual environment and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Start Docker Compose

```bash
docker-compose up -d
```

Wait for Postgres+PostGIS to be ready (check health status).

### 3. Verify database

```bash
# Connect to database
docker exec -it stockflow-db psql -U stockflow -d stockflow

# Verify PostGIS is enabled
SELECT PostGIS_Version();

# List tables
\dt

# Exit
\q
```

You should see:
- PostGIS version displayed
- Tables: stores, warehouses, items, inventory, demand_history, delivery_schedules, orders, transfers

### 4. Environment

Set up `.env` file with:

```
DATABASE_URL=postgresql://stockflow:stockflow@localhost:5432/stockflow
DEMO_MODE=true
SIMULATION_SPEED_MS=4500
LIVE_SIGNALS_ENABLED=true
LIVE_SIGNAL_CACHE_SECONDS=1800
LIVE_API_TIMEOUT_SECONDS=3.0
NWS_USER_AGENT=StockFlow/1.0 (your-email@example.com)
ANTHROPIC_API_KEY=
```

### 5. Seed Database

```bash
# Activate venv first
source venv/bin/activate

# Seed synthetic data
python -m data.seed

# Verify queries work
python -m data.seed --check
```

This creates 15 stores, 3 warehouses, 15 items, 180 days of demand history.

### 6. Run Backtest

```bash
# Compare forecasters + agent strategies
python -m evals.run_backtest

# Custom config
python -m evals.run_backtest --train-days 90 --test-days 60
```

Shows scorecard for:
- MovingAverage baseline
- SeasonalNaive baseline
- Replenishment agent (expiry-aware, variance-based par)
- Phase 4 (agent + cross-location transfers)

### 7. Run API

```bash
# Local dev
python -m api.run --reload

# Or via Docker Compose (includes Prometheus + Grafana)
docker compose up -d
```

Frontend / agent simulator: http://localhost:8000
API Docs: http://localhost:8000/docs
Grafana: http://localhost:3000 (admin/admin)
Prometheus: http://localhost:9090

API endpoints:
- `GET /health` - Health check
- `GET /demo/state` - Full live simulator state
- `POST /demo/tick` - Advance one simulated day and run agents
- `POST /demo/reset` - Reset the demo to the synthetic baseline
- `POST /demo/scenario/{scenario_name}` - Load `weekend-rush`, `game-day-spike`, `delivery-delay`, `expiry-rescue`, or `store-to-store-transfer`
- `GET /agents/events` - Recent agent timeline
- `GET /agents/reasoning-traces` - LangGraph tool-call traces
- `GET /agents/decisions/pending` - Pending human approval decisions
- `GET /live/signals` - Current free weather/holiday demand signals
- `POST /live/signals/refresh` - Force refresh free live signals
- `POST /agents/decisions/{id}/approve` - Approve an agent decision idempotently
- `POST /agents/decisions/{id}/reject` - Reject an agent decision idempotently
- `GET /metrics/demo-impact` - Before/after proof metrics
- `GET /stores` - List stores
- `GET /inventory/{store_id}` - Store inventory
- `POST /replenishment/run` - Run agent for store-item
- `GET /orders/pending` - Pending orders
- `POST /orders/{id}/approve` - Approve order
- `POST /explain/stockouts` - Explain chronic stockouts
- `GET /metrics/prometheus` - Prometheus metrics

## Free Live Deployment

The repo includes a `render.yaml` Blueprint for a no-cost Render deployment:

- Docker web service on Render's free web-service plan.
- Render Postgres on the free database plan.
- PostGIS is enabled by `data/schema/init.sql`.
- `scripts/bootstrap_hosted.py` creates schema and synthetic seed data on first boot.
- The app reads Render's `PORT` environment variable automatically.

Deploy path:

1. Push this repo to GitHub.
2. In Render, choose **New > Blueprint**.
3. Connect `pavanbobba09/StockFlow`.
4. Select the free web service and free Postgres resources from `render.yaml`.
5. Leave `ANTHROPIC_API_KEY` empty unless you want optional LLM explanations.
6. Open the generated `https://...onrender.com` URL after deploy.

The live app may cold-start on the free plan. That is acceptable for a portfolio
demo; the first request can take longer, then the simulator runs normally.

## Project Structure

```
StockFlow/
├── forecasting/         # Time-series models
├── agents/              # LangGraph agents
├── api/                 # FastAPI endpoints
├── data/                # Schema + synthetic data gen
├── evals/               # Backtest harness, metrics
├── tests/               # Tests
├── CLAUDE.md            # Project context
├── ARCHITECTURE.md      # Architecture docs
├── WORK_BREAKDOWN.md    # Phased plan
└── RULES.md             # Coding rules
```

## Recruiter Demo Flow

1. Open `http://localhost:8000`.
2. Pick a scenario such as **Weekend Rush** or **Expiry Rescue**.
3. Click **Run Day** or **Auto Play**.
4. Watch restaurant towers change color as inventory risk changes.
5. Review the agent timeline to see each agent's reasoning.
6. Approve or reject order, transfer, markdown, and donation decisions.
7. Compare **Without Agents vs With Agents** metrics.

## Development Phases

- **Phase 0**: ✅ Setup - containers, structure, schema
- **Phase 1**: ✅ Data foundation - synthetic data generator
- **Phase 2**: ✅ Forecasting + eval harness
- **Phase 3**: ✅ Replenishment agent
- **Phase 4**: ✅ Transfer/waste agent
- **Phase 5**: ✅ API + observability (current)
- **Phase 6**: ✅ Geo map frontend
- **Phase 7**: 🔜 Go live (hosted deployment)

See [WORK_BREAKDOWN.md](WORK_BREAKDOWN.md) for details.

## Key Principles

- Evals first. Backtest harness proves it works.
- Small composable agents. One job each.
- Idempotent tool calls. No double-orders on retries.
- Thin end-to-end slice first, then widen.
- Human in the loop on mutations. Agents propose, humans approve.

## License

MIT
