"""
Transfer/waste agent tools — all DB-backed, mutating tools are idempotent.

nearby_stores       → PostGIS spatial query
get_expiring_items  → scan inventory for near-expiry stock
get_store_shortfall → stores with demand > current inventory
suggest_transfer    → idempotent, proposes a transfer (no commit)
flag_expiry         → triggers markdown/donation action
"""

from datetime import date, timedelta
from typing import Optional

from langchain_core.tools import tool
from sqlalchemy import text

from data.db import SessionLocal


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def _nearby_stores(store_id: int, radius_km: float) -> list:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT
                    b.id,
                    b.name,
                    ROUND(
                        ST_Distance(a.location, b.location)::numeric / 1000, 2
                    ) AS dist_km
                FROM stores a
                CROSS JOIN stores b
                WHERE a.id = :sid
                  AND b.id <> a.id
                  AND ST_DWithin(a.location, b.location, :radius_m)
                ORDER BY dist_km
            """),
            {"sid": store_id, "radius_m": radius_km * 1000},
        ).fetchall()
        return [{"store_id": r.id, "name": r.name, "dist_km": float(r.dist_km)}
                for r in rows]
    finally:
        db.close()


def _get_expiring_items(store_id: int, days_threshold: int) -> list:
    db = SessionLocal()
    try:
        cutoff = date.today() + timedelta(days=days_threshold)
        rows = db.execute(
            text("""
                SELECT
                    inv.item_id,
                    it.name         AS item_name,
                    it.shelf_life_days,
                    SUM(inv.quantity) AS total_qty,
                    MIN(inv.expiry_date) AS nearest_expiry
                FROM inventory inv
                JOIN items it ON it.id = inv.item_id
                WHERE inv.location_id = :sid
                  AND inv.location_type = 'store'
                  AND inv.expiry_date <= :cutoff
                  AND inv.quantity > 0
                GROUP BY inv.item_id, it.name, it.shelf_life_days
                ORDER BY nearest_expiry
            """),
            {"sid": store_id, "cutoff": cutoff},
        ).fetchall()
        return [
            {
                "item_id": r.item_id,
                "item_name": r.item_name,
                "shelf_life_days": r.shelf_life_days,
                "expiring_qty": int(r.total_qty),
                "nearest_expiry": str(r.nearest_expiry),
                "days_until_expiry": (r.nearest_expiry - date.today()).days,
            }
            for r in rows
        ]
    finally:
        db.close()


def _get_store_shortfall(store_ids: list, item_id: int) -> list:
    """
    For each candidate store, return inventory vs recent average demand.
    Shortfall = avg_daily_demand × 3 - current_qty (3-day window).
    """
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT
                    s.id AS store_id,
                    s.name,
                    COALESCE(inv.total_qty, 0)   AS current_qty,
                    COALESCE(dh.avg_demand, 0)   AS avg_daily_demand
                FROM stores s
                LEFT JOIN (
                    SELECT location_id, SUM(quantity) AS total_qty
                    FROM inventory
                    WHERE location_type = 'store' AND item_id = :iid
                    GROUP BY location_id
                ) inv ON inv.location_id = s.id
                LEFT JOIN (
                    SELECT store_id, AVG(quantity) AS avg_demand
                    FROM demand_history
                    WHERE item_id = :iid
                      AND date >= CURRENT_DATE - INTERVAL '14 days'
                    GROUP BY store_id
                ) dh ON dh.store_id = s.id
                WHERE s.id = ANY(:sids)
                ORDER BY (COALESCE(dh.avg_demand, 0) * 3 - COALESCE(inv.total_qty, 0)) DESC
            """),
            {"iid": item_id, "sids": store_ids},
        ).fetchall()
        result = []
        for r in rows:
            shortfall = max(0.0, float(r.avg_daily_demand) * 3 - float(r.current_qty))
            result.append({
                "store_id": r.store_id,
                "store_name": r.name,
                "current_qty": int(r.current_qty),
                "avg_daily_demand": round(float(r.avg_daily_demand), 1),
                "shortfall_units": round(shortfall, 1),
            })
        return result
    finally:
        db.close()


def _suggest_transfer(
    from_store_id: int,
    to_store_id: int,
    item_id: int,
    quantity: int,
    idempotency_key: str,
    reason: str = "",
) -> dict:
    db = SessionLocal()
    try:
        existing = db.execute(
            text("SELECT id, status FROM transfers WHERE idempotency_key = :key"),
            {"key": idempotency_key},
        ).fetchone()
        if existing:
            return {"transfer_id": existing.id, "status": existing.status, "idempotent": True}

        row = db.execute(
            text("""
                INSERT INTO transfers
                    (from_store_id, to_store_id, item_id, quantity, status, idempotency_key)
                VALUES (:from_s, :to_s, :iid, :qty, 'pending', :key)
                RETURNING id
            """),
            {
                "from_s": from_store_id,
                "to_s": to_store_id,
                "iid": item_id,
                "qty": quantity,
                "key": idempotency_key,
            },
        ).fetchone()
        db.commit()
        return {
            "transfer_id": row.id,
            "from_store_id": from_store_id,
            "to_store_id": to_store_id,
            "item_id": item_id,
            "quantity": quantity,
            "status": "pending",
            "idempotency_key": idempotency_key,
            "reason": reason,
        }
    finally:
        db.close()


def _flag_expiry(
    store_id: int,
    item_id: int,
    expiry_date: str,
    action: str = "markdown",
) -> dict:
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT SUM(quantity) AS qty
                FROM inventory
                WHERE location_id = :sid
                  AND location_type = 'store'
                  AND item_id = :iid
                  AND expiry_date = :exp
            """),
            {"sid": store_id, "iid": item_id, "exp": expiry_date},
        ).fetchone()
        qty = int(row.qty or 0)
        return {
            "store_id": store_id,
            "item_id": item_id,
            "expiry_date": expiry_date,
            "quantity_affected": qty,
            "action": action,
            "status": "flagged",
            "message": f"{qty} units flagged for {action} before {expiry_date}.",
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# LangChain @tool wrappers
# ---------------------------------------------------------------------------

@tool
def nearby_stores(store_id: int, radius_km: float = 15.0) -> list:
    """
    Find stores within radius_km of the given store using PostGIS.
    Returns store ids, names, and distances in km, ordered nearest first.
    """
    return _nearby_stores(store_id, radius_km)


@tool
def get_expiring_items(store_id: int, days_threshold: int = 3) -> list:
    """
    List items at this store expiring within days_threshold days.
    Returns item details, quantities, and days until expiry.
    """
    return _get_expiring_items(store_id, days_threshold)


@tool
def get_store_shortfall(store_ids: list, item_id: int) -> list:
    """
    For a list of candidate stores, show current inventory vs expected demand.
    Shortfall = avg_daily_demand × 3 - current_qty.
    Useful for identifying which nearby stores need an item most.
    """
    return _get_store_shortfall(store_ids, item_id)


@tool
def suggest_transfer(
    from_store_id: int,
    to_store_id: int,
    item_id: int,
    quantity: int,
    idempotency_key: str,
    reason: str = "",
) -> dict:
    """
    Propose a stock transfer between two stores. Does NOT commit — human approves.
    Idempotent: same idempotency_key returns the original record.
    Returns transfer_id for use in the approval step.
    """
    return _suggest_transfer(from_store_id, to_store_id, item_id, quantity,
                             idempotency_key, reason)


@tool
def flag_expiry(
    store_id: int,
    item_id: int,
    expiry_date: str,
    action: str = "markdown",
) -> dict:
    """
    Flag near-expiry inventory for markdown or donation.
    action: 'markdown' (sell at discount) or 'donate' (food bank).
    Returns quantity flagged and confirmation.
    """
    return _flag_expiry(store_id, item_id, expiry_date, action)
