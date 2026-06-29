# StockFlow Resume Framing

## Project Title

**StockFlow: Multi-Agent Supply Chain Simulator for Franchise Restaurants**

## One-Line Resume Bullet

Built a multi-agent inventory optimization system for franchise restaurants that forecasts demand, detects low-stock and expiry risk, proposes replenishment orders and nearby store-to-store transfers, exposes MCP tools for AI-client integration, and measures impact through a backtest scorecard.

## Strong Resume Bullets

- Designed a LangGraph multi-agent workflow for restaurant supply chains, including inventory monitoring, demand forecasting, replenishment planning, waste prevention, baseline scoring, and human approval.
- Built an eval-first backtest harness to compare baseline forecasting and agent strategies across stockout rate, fill rate, waste rate, forecast WAPE/MAPE, and transfer impact.
- Implemented idempotent decision approval flows so retried order and transfer actions cannot create duplicate business mutations.
- Modeled perishable inventory with shelf-life constraints, expiry risk, FIFO-style consumption, and waste/markdown/donation decision paths.
- Added geospatial transfer logic to recommend moving excess food from overstocked restaurants to nearby shortage-risk restaurants before placing new supplier orders.
- Built a live operations-room frontend that visualizes franchise inventory risk, agent reasoning, proposed transfers, supplier orders, and before/after supply-chain metrics.
- Persisted agent reasoning traces showing tool inputs, observations, and decisions for each simulation tick.
- Added a Model Context Protocol adapter so external AI clients can inspect live state, call simulator tools, read reasoning traces, and use reusable supply-chain prompts.

## Interview Explanation

StockFlow solves a real franchise supply-chain problem: restaurants can lose money from both stockouts and food waste. Ordering too little loses sales; ordering too much creates spoilage. The project uses small specialized agents instead of one large generic agent:

- The **Inventory Watcher Agent** checks live restaurant stock after simulated customer demand.
- The **Demand Forecast Agent** predicts near-term demand from recent history and scenario pressure.
- The **Replenishment Agent** proposes how much to order before the next delivery window.
- The **Transfer/Waste Agent** finds nearby restaurants where expiring or excess stock can be used.
- The **Manager Approval Agent** keeps humans in control of final business actions.

The important engineering point is that agents do not directly mutate business state. They create auditable decisions with reasons and expected impact. Approvals are idempotent, so a retry or double-click does not duplicate an order or transfer.

## Technical Depth To Mention

- **Backend**: FastAPI, Python, SQLAlchemy, Postgres/PostGIS.
- **Agents**: LangGraph orchestration, deterministic decision tools, reasoning traces, plus optional LLM explanations.
- **AI integration**: MCP stdio and HTTP JSON-RPC endpoints exposing tools, resources, and prompts for external AI clients.
- **Forecasting**: moving average and seasonal naive baselines with backtest comparison.
- **Geospatial logic**: nearby-store matching for transfer recommendations.
- **Evals**: stockout rate, fill rate, waste rate, forecast error, transfer units, and estimated profit saved.
- **Safety**: human-in-the-loop approvals and idempotency keys for mutations.
- **Frontend**: live map/operations-room visualization for explaining agent activity.

## What Makes It More Than A Demo

The live UI is only the visualization layer. The core project value is the decision engine:

- it models uncertain demand,
- handles perishable inventory,
- compares competing objectives,
- coordinates multiple restaurant locations,
- records durable agent decisions,
- requires human approval for mutations,
- and proves impact through metrics.

## Best GitHub Description

AI-agent supply-chain simulator for franchise restaurants: demand forecasting, replenishment planning, geospatial transfers, expiry prevention, human approvals, and eval-first stockout/waste metrics.

## Best Portfolio Description

StockFlow is a multi-agent supply-chain system for franchise restaurants. It simulates customer demand across locations, tracks perishable inventory, forecasts weekend rushes, proposes replenishment orders, and coordinates store-to-store transfers to reduce both stockouts and food waste. A live map makes each agent's reasoning visible while the backend records auditable, idempotent decisions.
