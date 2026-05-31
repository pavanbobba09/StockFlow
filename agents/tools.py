"""
Agent tools — every mutating tool is idempotent via idempotency_key.

get_stock, get_forecast, get_par_levels → read-only
draft_order, place_order → mutating, idempotent
"""

from datetime import date, timedelta
from typing import Optional
import uuid
import numpy as np

from langchain_core.tools import tool
from sqlalchemy import text

from data.db import SessionLocal
from forecasting.forecasters import SeasonalNaiveForecaster


# ---------------------------------------------------------------------------
# Core functions (plain Python — callable from backtest and agent both)
# ---------------------------------------------------------------------------

def _stock(store_id: int, item_id: int) -> dict:
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT
                    COALESCE(SUM(quantity), 0)   AS total_qty,
                    MIN(expiry_date)             AS nearest_expiry,
                    COUNT(*)                     AS batch_count
                FROM inventory
                WHERE location_id = :sid
                  AND location_type = 'store'
                  AND item_id = :iid
            """),
            {"sid": store_id, "iid": item_id},
        ).fetchone()
        return {
            "store_id": store_id,
            "item_id": item_id,
            "quantity": int(row.total_qty),
            "nearest_expiry": str(row.nearest_expiry) if row.nearest_expiry else None,
            "batch_count": int(row.batch_count),
        }
    finally:
        db.close()


def _forecast(store_id: int, item_id: int, horizon: int) -> dict:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT date, quantity FROM demand_history
                WHERE store_id = :sid AND item_id = :iid
                ORDER BY date DESC LIMIT 90
            """),
            {"sid": store_id, "iid": item_id},
        ).fetchall()
        if not rows:
            return {"predictions": [0.0] * horizon, "total": 0.0, "horizon": horizon}
        dates = [r.date for r in reversed(rows)]
        qtys  = [r.quantity for r in reversed(rows)]
        fc = SeasonalNaiveForecaster(k=4)
        fc.fit(dates, qtys)
        preds = fc.predict(horizon)
        return {
            "predictions": [round(p, 1) for p in preds],
            "total": round(sum(preds), 1),
            "horizon": horizon,
            "avg_daily": round(np.mean(qtys), 1),
            "std_daily": round(float(np.std(qtys)), 1),
        }
    finally:
        db.close()


def _par_levels(store_id: int, item_id: int) -> dict:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT dh.quantity, ds.weekday
                FROM demand_history dh
                JOIN delivery_schedules ds ON ds.store_id = dh.store_id
                WHERE dh.store_id = :sid AND dh.item_id = :iid
                ORDER BY dh.date DESC LIMIT 90
            """),
            {"sid": store_id, "iid": item_id},
        ).fetchall()
        if not rows:
            return {"par": 50, "store_id": store_id, "item_id": item_id}

        qtys = [r.quantity for r in rows]
        # delivery_count gives frequency
        delivery_days_per_week = db.execute(
            text("SELECT COUNT(DISTINCT weekday) FROM delivery_schedules WHERE store_id = :sid"),
            {"sid": store_id},
        ).scalar() or 2
        gap_days = 7 // max(1, delivery_days_per_week)

        mean = np.mean(qtys)
        std  = np.std(qtys)
        # par = (mean + 1.5σ safety stock) × days-to-next-delivery
        par = max(1, int((mean + 1.5 * std) * gap_days))

        shelf = db.execute(
            text("SELECT shelf_life_days FROM items WHERE id = :iid"),
            {"iid": item_id},
        ).scalar() or 7

        return {
            "store_id": store_id,
            "item_id": item_id,
            "par": par,
            "shelf_life_days": shelf,
            "avg_daily_demand": round(float(mean), 1),
            "std_daily_demand": round(float(std), 1),
            "delivery_gap_days": gap_days,
        }
    finally:
        db.close()


def _draft_order(
    store_id: int,
    item_id: int,
    quantity: int,
    idempotency_key: str,
    reason: str = "",
) -> dict:
    db = SessionLocal()
    try:
        # Idempotency: return existing record if key already used
        existing = db.execute(
            text("SELECT id, status, quantity FROM orders WHERE idempotency_key = :key"),
            {"key": idempotency_key},
        ).fetchone()
        if existing:
            return {
                "order_id": existing.id,
                "status": existing.status,
                "quantity": existing.quantity,
                "idempotent": True,
            }
        row = db.execute(
            text("""
                INSERT INTO orders (store_id, item_id, quantity, status, idempotency_key)
                VALUES (:sid, :iid, :qty, 'pending', :key)
                RETURNING id
            """),
            {"sid": store_id, "iid": item_id, "qty": quantity, "key": idempotency_key},
        ).fetchone()
        db.commit()
        return {
            "order_id": row.id,
            "store_id": store_id,
            "item_id": item_id,
            "quantity": quantity,
            "status": "pending",
            "idempotency_key": idempotency_key,
            "reason": reason,
        }
    finally:
        db.close()


def _place_order(order_id: int, idempotency_key: str) -> dict:
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT id, status, idempotency_key FROM orders WHERE id = :oid"),
            {"oid": order_id},
        ).fetchone()
        if not row:
            return {"error": f"Order {order_id} not found"}
        if row.status == "approved":
            return {"order_id": order_id, "status": "approved", "idempotent": True}
        if row.idempotency_key != idempotency_key:
            return {"error": "Idempotency key mismatch"}
        db.execute(
            text("UPDATE orders SET status = 'approved' WHERE id = :oid"),
            {"oid": order_id},
        )
        db.commit()
        return {"order_id": order_id, "status": "approved", "message": "Order placed."}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# LangChain @tool wrappers (used by the LangGraph agent)
# ---------------------------------------------------------------------------

@tool
def get_stock(store_id: int, item_id: int) -> dict:
    """
    Get current inventory for a store-item pair.
    Returns quantity on hand, nearest expiry date, and batch count.
    """
    return _stock(store_id, item_id)


@tool
def get_forecast(store_id: int, item_id: int, horizon: int) -> dict:
    """
    Forecast demand for a store-item over the next `horizon` days.
    Uses seasonal naive model on last 90 days of history.
    Returns per-day predictions, total, and demand statistics.
    """
    return _forecast(store_id, item_id, horizon)


@tool
def get_par_levels(store_id: int, item_id: int) -> dict:
    """
    Get the par (target) stock level for a store-item.
    Par = (mean + 1.5σ safety stock) × delivery gap days.
    Also returns shelf life and delivery schedule info.
    """
    return _par_levels(store_id, item_id)


@tool
def draft_order(
    store_id: int,
    item_id: int,
    quantity: int,
    idempotency_key: str,
    reason: str = "",
) -> dict:
    """
    Draft a replenishment order. Does NOT commit — human must approve.
    Idempotent: calling with the same idempotency_key twice returns the original.
    Returns order_id to use when calling place_order after approval.
    """
    return _draft_order(store_id, item_id, quantity, idempotency_key, reason)


@tool
def place_order(order_id: int, idempotency_key: str) -> dict:
    """
    Commit a previously drafted order after human approval.
    Idempotent: safe to retry with the same idempotency_key.
    """
    return _place_order(order_id, idempotency_key)
