# StockFlow Project Walkthrough

## 1. What This Project Is

StockFlow is a multi-agent supply chain system for franchise restaurants.

Imagine a restaurant chain with many locations across a city or state. Every restaurant has food in storage, customer demand changes every day, and some food expires quickly. If a restaurant orders too little, it runs out of food and loses sales. If it orders too much, food expires and money is wasted.

StockFlow uses AI-agent workflows to help with this problem.

The agents:

- check inventory,
- forecast demand,
- detect low stock,
- detect food close to expiry,
- suggest store-to-store transfers,
- suggest replenishment orders,
- explain why they made the recommendation,
- wait for a human manager to approve or reject the action.

The 3D/live map is not the main product by itself. It is the visual layer that helps recruiters understand what the agents are doing.

## 2. Main Goal

The goal is to show a strong AI-agent project, not just a normal full-stack dashboard.

The project proves:

- agents can call tools,
- agents can coordinate with each other,
- agents can create auditable decisions,
- humans stay in control,
- business actions are idempotent,
- the system can be scored against a baseline,
- the result can reduce stockouts and food waste.

## 3. Frameworks And Why They Are Used

### Python

Python is used for the backend, agents, forecasting, and simulations.

Why:

- strong for AI/agent systems,
- strong for data and forecasting,
- simple to build backend services,
- works well with LangGraph, FastAPI, SQLAlchemy, and testing.

### FastAPI

FastAPI exposes the backend API.

Why:

- fast to build APIs,
- automatic API docs at `/docs`,
- good support for request/response models,
- clean structure for frontend and agent endpoints.

Example endpoints:

- `GET /demo/state`
- `POST /demo/tick`
- `GET /agents/reasoning-traces`
- `GET /agents/decisions/pending`
- `POST /agents/decisions/{id}/approve`

### LangGraph

LangGraph is used to build the multi-agent workflow.

Why:

- it makes the agent process visible as a graph,
- each agent can be a separate node,
- the workflow is easier to explain,
- it is better than one large unclear agent,
- recruiters can see real agent orchestration.

Current graph:

```text
Inventory Watcher Agent
  -> Demand Forecast Agent
  -> Transfer/Waste Agent
  -> Replenishment Agent
  -> Baseline Scorer Agent
```

### Postgres

Postgres stores the business data.

Why:

- reliable relational database,
- good for inventory, orders, decisions, and event history,
- supports transactions,
- good for production-style backend projects.

### PostGIS

PostGIS adds geospatial logic to Postgres.

Why:

- restaurants have latitude/longitude,
- transfer decisions depend on nearby stores,
- the system can ask which stores are close enough for a transfer.

Example:

```text
Store A has extra food expiring tomorrow.
Store B is 5 km away and is short on that item.
Transfer Agent recommends moving stock from A to B.
```

### SQLAlchemy

SQLAlchemy is used to connect Python code to Postgres.

Why:

- clean database sessions,
- easier SQL execution,
- standard Python database tool.

### Prometheus / Grafana

Prometheus and Grafana are included for observability.

Why:

- track metrics,
- show system health,
- make the project more production-shaped.

### React / Vite / TypeScript / TanStack Query / Zustand / Leaflet

The frontend uses a modern React operations-room UI.

Why:

- React components keep metrics, decisions, agents, traces, and map views reusable,
- Vite gives fast local development and simple production builds,
- TypeScript makes API state and decision objects safer to change,
- TanStack Query handles API fetching, mutations, cache refreshes, and live-state invalidation,
- Zustand keeps small UI state such as active scenario, autoplay, and stream status simple,
- Leaflet renders restaurants, warehouses, transfer routes, and risk signals.

The frontend shows:

- restaurant inventory risk,
- expiry risk,
- warehouse routes,
- store-to-store transfer routes,
- agent timeline,
- tool-call traces,
- pending manager decisions,
- before/after metrics.

### Free Live APIs

StockFlow can use free public APIs to make the simulation react to real outside signals.

Current free APIs:

- **National Weather Service API**: live U.S. weather forecast by restaurant latitude/longitude.
- **Nager.Date API**: public holiday calendar for the United States.

Why:

- bad weather can change food demand,
- holidays can increase restaurant traffic,
- these APIs do not require paid API keys,
- the agents can show real external tool inputs.

The system caches these signals so the demo does not call external APIs too often.

## 4. Important Data In The System

The system stores:

- restaurants,
- warehouses,
- food items,
- inventory quantities,
- expiry dates,
- demand history,
- simulation ticks,
- customer demand events,
- agent events,
- agent reasoning traces,
- pending decisions,
- approval/rejection events,
- inventory movements,
- waste events.

## 5. How The System Starts

First, synthetic data is created.

The seed data includes:

- restaurant locations,
- warehouse locations,
- food items,
- shelf life for each food item,
- inventory at each restaurant,
- inventory at each warehouse,
- historical demand,
- delivery schedules.

This lets the project work without real company data.

## 6. What Happens When The User Opens The App

The user opens:

```text
http://localhost:8000
```

The frontend calls:

```text
GET /demo/state
```

The backend returns:

- current simulation day,
- active scenario,
- restaurants,
- warehouses,
- inventory risk,
- expiry risk,
- agent list,
- recent agent events,
- reasoning traces,
- pending decisions,
- proof metrics.

The frontend then renders:

- map,
- restaurant towers,
- warehouse hubs,
- agent cards,
- timeline,
- manager decisions,
- metrics.

## 7. What Happens When The User Clicks Run Day

The frontend calls:

```text
POST /demo/tick
```

This advances the simulation by one day.

The backend does these steps:

1. creates a new simulation tick,
2. applies the selected scenario,
3. loads free live weather/holiday signals,
4. adjusts demand pressure from those live signals,
5. expires old inventory,
6. simulates customer demand,
7. reduces restaurant inventory,
8. records stockouts if demand cannot be fulfilled,
9. runs the LangGraph multi-agent decision engine,
10. creates agent reasoning traces,
11. creates pending decisions,
12. updates metrics,
13. returns the new state to the frontend.

## 8. Step-By-Step Agent Flow

### Step 1: Inventory Watcher Agent

This agent checks restaurant inventory.

It calls a tool like:

```text
scan_inventory_risk
```

It checks:

- current stock,
- expected short-term demand,
- whether the store is safe, low, or critical.

Example output:

```text
Midtown has 80 chicken units.
Three-day forecast is 240 units.
Risk is high.
```

Then it passes risky items to the next agent.

### Step 2: Demand Forecast Agent

This agent predicts demand.

It calls:

```text
forecast_risk_items
```

It uses:

- historical demand,
- scenario multiplier,
- live weather/holiday multiplier,
- weekend or event pressure.

Example:

```text
Weekend Rush increases expected demand.
Chicken demand for Midtown is projected at 240 units.
```

Then it passes forecasted shortages to the transfer and replenishment agents.

### Step 3: Transfer/Waste Agent

This agent tries to reduce waste before ordering more food.

It calls:

```text
find_transfer_candidates
```

It checks:

- which stores have food close to expiry,
- which nearby stores are short,
- distance between stores,
- transfer cost,
- waste cost.

Example:

```text
Downtown has 60 chicken units expiring tomorrow.
Buckhead is short 50 chicken units.
Buckhead is nearby.
Transfer cost is cheaper than waste.
Recommend transferring 50 units.
```

This creates a pending transfer decision.

### Step 4: Replenishment Agent

This agent decides how much food should be ordered from the warehouse or supplier.

It calls:

```text
estimate_replenishment_need
```

It checks:

- forecasted demand,
- current inventory,
- safety stock,
- shelf life,
- pending transfer decisions.

Example:

```text
Forecast need: 240 units
Current stock: 80 units
Pending transfer: 50 units
Remaining shortage: 110 units
Recommend ordering 110 units
```

This creates a pending order decision.

### Step 5: Baseline Scorer Agent

This agent scores the system.

It calls:

```text
score_against_baseline
```

It compares:

- what would happen without agents,
- what happened with agents,
- stockouts avoided,
- waste reduced,
- transfer units,
- estimated profit saved.

Example:

```text
Without agents: 120 stockout units, 80 waste units
With agents: 45 stockout units, 25 waste units
```

This makes the project measurable.

## 9. What Is A Reasoning Trace

A reasoning trace records how an agent made a decision.

Each trace stores:

- agent name,
- tool name,
- input summary,
- observation,
- decision.

Example:

```text
Agent: Transfer/Waste Agent
Tool: find_transfer_candidates
Input: scenario=expiry-rescue
Observation: Found 3 transfer candidates
Decision: Prefer transfer before supplier order
```

This is important because it shows the system is not just showing random UI cards. The agents are calling tools and recording their process.

## 10. What Is A Pending Decision

Agents do not directly change important business state.

They create pending decisions such as:

- order food,
- transfer food,
- markdown food,
- donate food.

Example:

```text
Decision: Transfer 50 chicken units
From: Downtown
To: Buckhead
Reason: Downtown has near-expiry food and Buckhead is short
Expected impact: reduces waste and avoids new order
```

The manager must approve or reject it.

## 11. Why Human Approval Matters

In a real supply chain, placing an order or moving food costs money.

The system should not blindly auto-commit every agent suggestion.

So StockFlow follows this rule:

```text
Agents propose. Humans approve.
```

This makes the project safer and more realistic.

## 12. What Happens When A Decision Is Approved

The frontend calls:

```text
POST /agents/decisions/{id}/approve
```

The backend:

1. checks if the decision exists,
2. checks if it is still pending,
3. records an approval event,
4. applies the inventory movement,
5. updates the decision status,
6. updates metrics.

For an order:

```text
warehouse inventory decreases
restaurant inventory increases
```

For a transfer:

```text
source restaurant inventory decreases
target restaurant inventory increases
```

For a markdown or donation:

```text
expiry-risk inventory is removed
waste risk decreases
```

## 13. What Idempotency Means

Idempotency prevents duplicate business actions.

Example problem:

```text
Manager clicks approve.
Network is slow.
The request retries.
Without idempotency, the order could be placed twice.
```

StockFlow uses idempotency keys so repeated approval calls return the original result instead of applying the action again.

This is important because duplicate orders or transfers can cost real money.

## 14. How The Frontend Explains The Agents

The frontend shows:

- restaurant nodes,
- inventory tower height,
- risk colors,
- warehouse hubs,
- transfer/order routes,
- pending decisions,
- agent team,
- tool traces,
- agent timeline,
- metrics.

Colors:

- green = healthy,
- yellow = low stock,
- red = stockout risk,
- purple = expiry risk,
- blue route = store-to-store transfer,
- orange route = warehouse order.

This helps a recruiter understand the project quickly.

## 15. How This Is Different From A Normal Full-Stack App

A normal full-stack inventory app usually has:

- CRUD screens,
- tables,
- forms,
- manual inventory updates.

StockFlow has:

- multi-agent workflow,
- tool-calling traces,
- forecasting,
- geospatial transfer logic,
- perishable inventory constraints,
- human approval,
- idempotent mutations,
- baseline scoring,
- live visual explanation.

The frontend is not the main achievement. The main achievement is the agent decision system behind it.

## 16. How This Works Without An LLM

The core system works without an LLM because the business decisions are deterministic and testable.

The agents use tools and rules:

- inventory math,
- demand forecasts,
- shelf-life checks,
- distance calculations,
- transfer cost versus waste cost,
- approval state.

This is stronger than asking an LLM to guess orders.

Optional LLM usage can be added later for:

- nicer explanations,
- anomaly summaries,
- manager chat,
- natural-language reports.

But the actual supply-chain decision should not depend on an LLM hallucinating a number.

## 17. Current Main API Endpoints

```text
GET  /demo/state
POST /demo/tick
POST /demo/reset
POST /demo/scenario/{scenario_name}
GET  /agents/events
GET  /agents/reasoning-traces
GET  /agents/decisions/pending
GET  /live/signals
POST /live/signals/refresh
POST /agents/decisions/{id}/approve
POST /agents/decisions/{id}/reject
GET  /metrics/demo-impact
```

## 18. Current Scenarios

The project supports scenarios such as:

- Weekend Rush,
- Game Day Spike,
- Delivery Delay,
- Expiry Rescue,
- Store-to-Store Transfer.

These scenarios make the agents react to different supply-chain problems.

## 19. What To Say In An Interview

Simple explanation:

```text
StockFlow is a multi-agent supply-chain system for franchise restaurants.
It simulates customer demand, tracks perishable inventory, forecasts shortages,
and uses LangGraph agents to propose orders, transfers, markdowns, or donations.
Each agent calls tools, stores reasoning traces, and creates auditable decisions.
Humans approve mutations, and idempotency prevents duplicate orders.
The system is scored against a no-agent baseline to show stockouts and waste reduced.
```

## 20. Next Improvements

Good next steps:

1. build a repeatable evaluation script,
2. run multiple scenarios automatically,
3. compare no-agent baseline vs agent system,
4. export metrics to CSV/JSON,
5. generate charts for resume/portfolio,
6. add optional LLM explanations,
7. deploy the live simulator publicly,
8. add real external signals such as weather or public events.

## 21. Rough Estimate For Next Script

A useful next script would be:

```text
scripts/run_agent_eval.py
```

It should:

1. reset the demo,
2. seed or verify data,
3. run a scenario for several simulated days,
4. run the LangGraph agents,
5. auto-approve safe decisions,
6. compare baseline vs agent results,
7. export a report.

Rough time estimate:

- simple version: 1 day,
- polished report version: 2 to 3 days,
- full multi-seed evaluation suite: 4 to 5 days.

## 22. Best Resume Summary

StockFlow is a LangGraph-powered multi-agent supply-chain simulator for franchise restaurants. It forecasts demand, detects stockout and expiry risk, recommends replenishment orders and geospatial transfers, records auditable reasoning traces, requires human approval for mutations, and scores agent decisions against a baseline to prove reduced waste and stockouts.
