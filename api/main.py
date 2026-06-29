"""
FastAPI app — exposes proposals, state, metrics, and explainer outputs.

Endpoints:
  GET  /health              — health check
  GET  /stores              — list all stores
  GET  /items               — list all items
  GET  /inventory/{store_id} — current inventory for a store
  POST /replenishment/run   — run replenishment agent for store-item
  GET  /orders/pending      — pending draft orders awaiting approval
  POST /orders/{order_id}/approve — approve a draft order
  POST /orders/{order_id}/reject  — reject a draft order
  GET  /metrics/scorecard   — current metrics snapshot
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from pathlib import Path

from data.db import get_db, check_connection, SessionLocal
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter, Histogram
from starlette.responses import Response

from agents.demo_simulator import (
    apply_scenario,
    approve_decision,
    demo_impact_metrics,
    ensure_demo_schema,
    get_agent_events,
    get_demo_state,
    get_pending_decisions,
    get_reasoning_traces,
    reject_decision,
    reset_demo,
    run_demo_tick,
    set_autoplay,
)
from agents.live_signals import get_live_signal_summary

app = FastAPI(
    title="StockFlow API",
    description="Multi-agent inventory management for food chains",
    version="0.1.0",
)

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "frontend" / "static"), name="static")


@app.on_event("startup")
def startup():
    """Prepare demo tables when the database is reachable."""
    db = next(get_db())
    try:
        ensure_demo_schema(db)
    except Exception:
        # /health and endpoint-level errors will expose DB problems; do not make
        # static frontend startup depend on Postgres being ready.
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

replenishment_runs = Counter(
    "stockflow_replenishment_runs_total",
    "Total replenishment agent runs",
    ["store_id", "item_id"],
)
replenishment_duration = Histogram(
    "stockflow_replenishment_duration_seconds",
    "Replenishment agent run duration",
)
orders_approved = Counter("stockflow_orders_approved_total", "Orders approved by humans")
orders_rejected = Counter("stockflow_orders_rejected_total", "Orders rejected by humans")


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class StoreOut(BaseModel):
    id: int
    name: str
    lat: float
    lng: float


class ItemOut(BaseModel):
    id: int
    name: str
    shelf_life_days: int


class InventoryOut(BaseModel):
    item_id: int
    item_name: str
    quantity: int
    expiry_date: Optional[date]


class ReplenishmentRequest(BaseModel):
    store_id: int
    item_id: int


class ReplenishmentResponse(BaseModel):
    draft_order_id: Optional[int]
    order_needed: bool
    summary: str


class OrderOut(BaseModel):
    id: int
    store_id: int
    item_id: int
    quantity: int
    status: str
    idempotency_key: str
    created_at: datetime


class MetricsOut(BaseModel):
    total_stores: int
    total_items: int
    total_inventory_units: int
    pending_orders: int
    fill_rate_7d: Optional[float] = None
    waste_rate_7d: Optional[float] = None


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/")
def serve_frontend():
    """Serve the frontend."""
    return FileResponse(BASE_DIR / "frontend" / "index.html")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    try:
        check_connection()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database error: {e}")


# ---------------------------------------------------------------------------
# AI-agent demo simulator
# ---------------------------------------------------------------------------

@app.get("/demo/state")
def demo_state(db: Session = Depends(get_db)):
    """Return the full recruiter-facing live simulation state."""
    try:
        return get_demo_state(db)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Demo state error: {e}")


@app.post("/demo/tick")
def demo_tick(db: Session = Depends(get_db)):
    """Advance the simulated franchise network by one day and run demo agents."""
    try:
        return run_demo_tick(db)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Demo tick error: {e}")


@app.post("/demo/reset")
def reset_demo_state(db: Session = Depends(get_db)):
    """Reset demo events, decisions, orders, transfers, and inventory baseline."""
    try:
        return reset_demo(db)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Demo reset error: {e}")


@app.post("/demo/autoplay/start")
def start_autoplay(db: Session = Depends(get_db)):
    """Mark demo autoplay as enabled for the frontend."""
    try:
        return set_autoplay(db, True)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Autoplay error: {e}")


@app.post("/demo/autoplay/stop")
def stop_autoplay(db: Session = Depends(get_db)):
    """Mark demo autoplay as disabled for the frontend."""
    try:
        return set_autoplay(db, False)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Autoplay error: {e}")


@app.post("/demo/scenario/{scenario_name}")
def load_scenario(scenario_name: str, db: Session = Depends(get_db)):
    """Load a named simulation scenario, such as weekend-rush or expiry-rescue."""
    try:
        return apply_scenario(db, scenario_name)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Scenario error: {e}")


@app.get("/agents/events")
def agent_events(limit: int = 50, db: Session = Depends(get_db)):
    """Return recent agent timeline events."""
    try:
        return get_agent_events(db, limit=limit)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Agent events error: {e}")


@app.get("/agents/reasoning-traces")
def agent_reasoning_traces(limit: int = 50, db: Session = Depends(get_db)):
    """Return LangGraph agent tool-call traces and observations."""
    try:
        return get_reasoning_traces(db, limit=limit)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Reasoning trace error: {e}")


@app.get("/live/signals")
def live_signals(db: Session = Depends(get_db)):
    """Return cached/free live signals that influence agent demand forecasts."""
    try:
        ensure_demo_schema(db)
        return get_live_signal_summary(db)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Live signals error: {e}")


@app.post("/live/signals/refresh")
def refresh_live_signals(db: Session = Depends(get_db)):
    """Force-refresh free live signals from public APIs."""
    try:
        ensure_demo_schema(db)
        summary = get_live_signal_summary(db, force_refresh=True)
        db.commit()
        return summary
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Live signal refresh error: {e}")


@app.get("/live/events")
async def live_events(request: Request):
    """
    Stream lightweight live updates for the operations-room UI.

    The frontend receives these Server-Sent Events through EventSource and then
    refreshes /demo/state. This keeps the stream small while preserving the
    existing state-rendering path.
    """

    async def event_generator():
        db = SessionLocal()
        last_signature = None
        try:
            ensure_demo_schema(db)
            while True:
                if await request.is_disconnected():
                    break
                payload = _live_event_payload(db)
                signature = json.dumps(payload["cursors"], sort_keys=True)
                if signature != last_signature:
                    last_signature = signature
                    yield _sse_event("stockflow-state", payload, event_id=str(payload["sequence"]))
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(2)
        finally:
            db.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _live_event_payload(db: Session) -> dict:
    row = db.execute(
        text("""
            SELECT
                COALESCE((SELECT MAX(id) FROM agent_events), 0) AS max_event_id,
                COALESCE((SELECT MAX(id) FROM agent_reasoning_traces), 0) AS max_trace_id,
                COALESCE((SELECT MAX(id) FROM agent_decisions), 0) AS max_decision_id,
                COALESCE((SELECT COUNT(*) FROM agent_decisions WHERE status = 'pending'), 0) AS pending_decisions,
                COALESCE((SELECT MAX(id) FROM simulation_ticks), 0) AS max_tick_id
        """)
    ).fetchone()
    sequence = max(
        int(row.max_event_id or 0),
        int(row.max_trace_id or 0),
        int(row.max_decision_id or 0),
        int(row.max_tick_id or 0),
    )
    return {
        "type": "state_changed",
        "sequence": sequence,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "cursors": {
            "max_event_id": int(row.max_event_id or 0),
            "max_trace_id": int(row.max_trace_id or 0),
            "max_decision_id": int(row.max_decision_id or 0),
            "pending_decisions": int(row.pending_decisions or 0),
            "max_tick_id": int(row.max_tick_id or 0),
        },
    }


def _sse_event(event_name: str, payload: dict, event_id: str | None = None) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append(f"data: {json.dumps(payload, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


@app.get("/agents/decisions/pending")
def pending_agent_decisions(db: Session = Depends(get_db)):
    """Return pending order, transfer, markdown, and donation decisions."""
    try:
        return get_pending_decisions(db)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Pending decisions error: {e}")


@app.post("/agents/decisions/{decision_id}/approve")
def approve_agent_decision(decision_id: int, db: Session = Depends(get_db)):
    """Approve an agent decision idempotently and apply its inventory effect."""
    try:
        return approve_decision(db, decision_id)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Decision approval error: {e}")


@app.post("/agents/decisions/{decision_id}/reject")
def reject_agent_decision(decision_id: int, db: Session = Depends(get_db)):
    """Reject an agent decision idempotently."""
    try:
        return reject_decision(db, decision_id)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Decision rejection error: {e}")


@app.get("/metrics/demo-impact")
def demo_impact(db: Session = Depends(get_db)):
    """Return before/after proof metrics for the agent simulator."""
    try:
        return demo_impact_metrics(db)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Demo metrics error: {e}")


# ---------------------------------------------------------------------------
# Stores & Items
# ---------------------------------------------------------------------------

@app.get("/stores", response_model=List[StoreOut])
def list_stores(db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT id, name, lat, lng FROM stores ORDER BY name")).fetchall()
    return [{"id": r.id, "name": r.name, "lat": r.lat, "lng": r.lng} for r in rows]


@app.get("/items", response_model=List[ItemOut])
def list_items(db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT id, name, shelf_life_days FROM items ORDER BY name")).fetchall()
    return [{"id": r.id, "name": r.name, "shelf_life_days": r.shelf_life_days} for r in rows]


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

@app.get("/inventory/{store_id}", response_model=List[InventoryOut])
def get_inventory(store_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT i.id, i.name, inv.quantity, inv.expiry_date
            FROM inventory inv
            JOIN items i ON i.id = inv.item_id
            WHERE inv.location_id = :sid AND inv.location_type = 'store'
            ORDER BY i.name
        """),
        {"sid": store_id},
    ).fetchall()
    return [
        {"item_id": r.id, "item_name": r.name, "quantity": r.quantity, "expiry_date": r.expiry_date}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Replenishment
# ---------------------------------------------------------------------------

@app.post("/replenishment/run", response_model=ReplenishmentResponse)
def run_replenishment(req: ReplenishmentRequest):
    """
    Run replenishment agent for store-item pair.
    Returns draft_order_id if order needed, None otherwise.
    """
    import time
    from agents.replenishment import run_replenishment_agent

    replenishment_runs.labels(store_id=req.store_id, item_id=req.item_id).inc()

    start = time.time()
    try:
        result = run_replenishment_agent(req.store_id, req.item_id)
        return ReplenishmentResponse(
            draft_order_id=result["draft_order_id"],
            order_needed=result["order_needed"],
            summary=result["summary"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {e}")
    finally:
        replenishment_duration.observe(time.time() - start)


# ---------------------------------------------------------------------------
# Orders (pending approval)
# ---------------------------------------------------------------------------

@app.get("/orders/pending", response_model=List[OrderOut])
def list_pending_orders(db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id, store_id, item_id, quantity, status, idempotency_key, created_at
            FROM orders
            WHERE status = 'pending'
            ORDER BY created_at DESC
        """)
    ).fetchall()
    return [
        {
            "id": r.id,
            "store_id": r.store_id,
            "item_id": r.item_id,
            "quantity": r.quantity,
            "status": r.status,
            "idempotency_key": r.idempotency_key,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@app.post("/orders/{order_id}/approve")
def approve_order(order_id: int, db: Session = Depends(get_db)):
    """Approve a pending draft order."""
    db.execute(
        text("UPDATE orders SET status = 'approved' WHERE id = :oid AND status = 'pending'"),
        {"oid": order_id},
    )
    db.commit()
    orders_approved.inc()
    return {"order_id": order_id, "status": "approved"}


@app.post("/orders/{order_id}/reject")
def reject_order(order_id: int, db: Session = Depends(get_db)):
    """Reject a pending draft order."""
    db.execute(
        text("UPDATE orders SET status = 'rejected' WHERE id = :oid AND status = 'pending'"),
        {"oid": order_id},
    )
    db.commit()
    orders_rejected.inc()
    return {"order_id": order_id, "status": "rejected"}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@app.get("/metrics/scorecard", response_model=MetricsOut)
def get_metrics(db: Session = Depends(get_db)):
    """Return current system-wide metrics."""
    total_stores = db.execute(text("SELECT COUNT(*) FROM stores")).scalar()
    total_items = db.execute(text("SELECT COUNT(*) FROM items")).scalar()
    total_inv = db.execute(text("SELECT COALESCE(SUM(quantity), 0) FROM inventory")).scalar()
    pending = db.execute(text("SELECT COUNT(*) FROM orders WHERE status = 'pending'")).scalar()

    return MetricsOut(
        total_stores=total_stores,
        total_items=total_items,
        total_inventory_units=total_inv,
        pending_orders=pending,
        fill_rate_7d=None,  # TODO: compute from demand_history + fulfilled
        waste_rate_7d=None,  # TODO: compute from expiry logs
    )


@app.get("/metrics/prometheus")
def prometheus_metrics():
    """Expose Prometheus metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# Anomaly Explainer
# ---------------------------------------------------------------------------

class ExplainStockoutsRequest(BaseModel):
    store_id: int
    item_id: int


class ExplainStockoutsResponse(BaseModel):
    explanation: str


@app.post("/explain/stockouts", response_model=ExplainStockoutsResponse)
def explain_stockouts(req: ExplainStockoutsRequest, db: Session = Depends(get_db)):
    """
    Explain chronic stockouts for a store-item pair.
    Looks at last 7 days of demand history and delivery schedule.
    """
    from datetime import datetime, timedelta
    from agents.anomaly_explainer import explain_chronic_stockouts

    # Find recent stockout dates (demand > fulfilled)
    # For simplicity, just check if demand_history shows high demand
    # In real system, track fulfilled vs demand separately
    cutoff = datetime.now().date() - timedelta(days=7)
    demand_rows = db.execute(
        text("""
            SELECT date, quantity
            FROM demand_history
            WHERE store_id = :sid AND item_id = :iid AND date >= :cutoff
            ORDER BY date DESC
        """),
        {"sid": req.store_id, "iid": req.item_id, "cutoff": cutoff},
    ).fetchall()

    # Get context
    store = db.execute(text("SELECT name FROM stores WHERE id = :sid"), {"sid": req.store_id}).fetchone()
    item = db.execute(text("SELECT name FROM items WHERE id = :iid"), {"iid": req.item_id}).fetchone()
    delivery_wds = db.execute(
        text("SELECT weekday FROM delivery_schedules WHERE store_id = :sid"),
        {"sid": req.store_id},
    ).fetchall()

    if not demand_rows:
        raise HTTPException(status_code=404, detail="No recent demand data")

    stockout_dates = [r.date for r in demand_rows if r.quantity > 30]  # heuristic
    avg_demand = sum(r.quantity for r in demand_rows) / len(demand_rows)

    context = {
        "item_name": item.name if item else f"Item {req.item_id}",
        "store_name": store.name if store else f"Store {req.store_id}",
        "delivery_weekdays": [r.weekday for r in delivery_wds],
        "avg_daily_demand": avg_demand,
        "par_level": int(avg_demand * 3.5 * 1.3),
    }

    explanation = explain_chronic_stockouts(
        req.store_id, req.item_id, stockout_dates, context
    )
    return ExplainStockoutsResponse(explanation=explanation)
