# StockFlow

Multi-agent inventory system for food chains. Forecasts demand, drafts replenishment orders, coordinates transfers between stores using geospatial reasoning. Cuts stockouts and waste together.

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
OPENAI_API_KEY=your_key_here
```

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

## Development Phases

- **Phase 0 (Current)**: Setup - containers, structure, schema
- **Phase 1**: Data foundation - synthetic data generator
- **Phase 2**: Forecasting + eval harness
- **Phase 3**: Replenishment agent (one store-item)
- **Phase 4**: Widen + transfer/waste agent
- **Phase 5**: Anomaly-explainer + API + observability
- **Phase 6**: 3D/geo frontend
- **Phase 7**: Go live (hosted deployment)

See [WORK_BREAKDOWN.md](WORK_BREAKDOWN.md) for details.

## Key Principles

- Evals first. Backtest harness proves it works.
- Small composable agents. One job each.
- Idempotent tool calls. No double-orders on retries.
- Thin end-to-end slice first, then widen.
- Human in the loop on mutations. Agents propose, humans approve.

## License

MIT
