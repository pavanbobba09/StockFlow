"""
Seed the database with synthetic data.

Usage:
    python -m data.seed           # fresh seed (drops existing data)
    python -m data.seed --check   # just verify queries work, no insert
"""

import sys
import argparse
from pathlib import Path
from sqlalchemy import inspect, text

from data.db import SessionLocal, engine
from data.models import (
    Store, Warehouse, Item, Inventory,
    DemandHistory, DeliverySchedule,
)
from data.synthetic import build_seed_data

SCHEMA_SQL = Path(__file__).resolve().parent / "schema" / "init.sql"


def apply_schema_if_needed():
    """Create core tables from init.sql when absent.

    Docker Compose applies init.sql at container creation, but a plain local
    Postgres (no Docker) starts empty, so seeding directly would fail.
    """
    if "stores" in inspect(engine).get_table_names(schema="public"):
        return
    print("Core tables missing — applying schema from init.sql...")
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        raw.commit()
    finally:
        raw.close()


def clear_tables(session):
    existing_tables = {
        r.table_name
        for r in session.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
        """)).fetchall()
    }
    # Delete in FK-safe order
    for table in [
        "approval_events", "inventory_movements", "agent_decisions",
        "agent_reasoning_traces",
        "agent_events", "simulated_demand_events", "waste_events",
        "simulation_ticks", "demo_inventory_baseline", "demo_state",
        "transfers", "orders", "inventory",
        "demand_history", "delivery_schedules",
        "stores", "warehouses", "items",
    ]:
        if table in existing_tables:
            session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    print("Cleared existing data.")


def seed(session, data: dict):
    # 1. Items
    item_objs = []
    for row in data["items"]:
        obj = Item(**row)
        session.add(obj)
        item_objs.append(obj)
    session.flush()
    item_ids = [obj.id for obj in item_objs]
    print(f"  Inserted {len(item_ids)} items.")

    # 2. Stores
    store_objs = []
    for row in data["stores"]:
        obj = Store(**row)
        session.add(obj)
        store_objs.append(obj)
    session.flush()
    store_ids = [obj.id for obj in store_objs]
    print(f"  Inserted {len(store_ids)} stores.")

    # 3. Warehouses
    wh_objs = []
    for row in data["warehouses"]:
        obj = Warehouse(**row)
        session.add(obj)
        wh_objs.append(obj)
    session.flush()
    wh_ids = [obj.id for obj in wh_objs]
    print(f"  Inserted {len(wh_ids)} warehouses.")

    # 4. Delivery schedules
    for row in data["delivery_schedules"]:
        session.add(DeliverySchedule(
            store_id=store_ids[row["store_idx"]],
            weekday=row["weekday"],
            cutoff_time=row["cutoff_time"],
        ))
    session.flush()
    print(f"  Inserted {len(data['delivery_schedules'])} delivery schedules.")

    # 5. Demand history (bulk)
    demand_rows = [
        {
            "store_id": store_ids[r["store_idx"]],
            "item_id": item_ids[r["item_idx"]],
            "date": r["date"],
            "quantity": r["quantity"],
        }
        for r in data["demand_history"]
    ]
    session.execute(
        text(
            "INSERT INTO demand_history (store_id, item_id, date, quantity) "
            "VALUES (:store_id, :item_id, :date, :quantity)"
        ),
        demand_rows,
    )
    print(f"  Inserted {len(demand_rows):,} demand history rows.")

    # 6. Inventory
    inv_rows = []
    for row in data["inventory"]:
        if row["location_type"] == "store":
            loc_id = store_ids[row["location_idx"]]
        else:
            loc_id = wh_ids[row["location_idx"]]
        session.add(Inventory(
            location_id=loc_id,
            location_type=row["location_type"],
            item_id=item_ids[row["item_idx"]],
            quantity=row["quantity"],
            expiry_date=row["expiry_date"],
        ))
    session.flush()
    print(f"  Inserted {len(data['inventory'])} inventory rows.")

    session.commit()
    return store_ids, item_ids, wh_ids


def verify_queries(session, store_ids, item_ids):
    """Run the three 'done when' queries from WORK_BREAKDOWN Phase 1."""

    # Q1: stock at store X
    store_id = store_ids[0]
    rows = session.execute(
        text("""
            SELECT i.name, inv.quantity, inv.expiry_date
            FROM inventory inv
            JOIN items i ON i.id = inv.item_id
            WHERE inv.location_id = :sid AND inv.location_type = 'store'
            ORDER BY i.name
        """),
        {"sid": store_id},
    ).fetchall()
    print(f"\n[Q1] Stock at store_id={store_id}  ({len(rows)} items):")
    for r in rows[:5]:
        print(f"     {r.name:<30} qty={r.quantity}  expiry={r.expiry_date}")

    # Q2: demand history for store X item Y
    item_id = item_ids[0]
    rows = session.execute(
        text("""
            SELECT date, quantity
            FROM demand_history
            WHERE store_id = :sid AND item_id = :iid
            ORDER BY date DESC
            LIMIT 7
        """),
        {"sid": store_id, "iid": item_id},
    ).fetchall()
    print(f"\n[Q2] Last 7 days demand — store={store_id} item={item_id}:")
    for r in rows:
        print(f"     {r.date}  qty={r.quantity}")

    # Q3: stores within 10 km (PostGIS)
    rows = session.execute(
        text("""
            SELECT
                b.id,
                b.name,
                ROUND(ST_Distance(a.location, b.location)::numeric / 1000, 2) AS dist_km
            FROM stores a
            CROSS JOIN stores b
            WHERE a.id = :sid
              AND b.id <> a.id
              AND ST_DWithin(a.location, b.location, 10000)
            ORDER BY dist_km
        """),
        {"sid": store_id},
    ).fetchall()
    print(f"\n[Q3] Stores within 10 km of store_id={store_id}  ({len(rows)} found):")
    for r in rows:
        print(f"     id={r.id}  {r.name:<30} {r.dist_km} km")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="Run verification queries only (skip seeding)")
    args = parser.parse_args()

    apply_schema_if_needed()

    session = SessionLocal()
    try:
        if not args.check:
            print("Building synthetic data...")
            data = build_seed_data(seed=42)
            print(f"  Stores: {len(data['stores'])}")
            print(f"  Warehouses: {len(data['warehouses'])}")
            print(f"  Items: {len(data['items'])}")
            print(f"  Demand rows: {len(data['demand_history']):,}")

            print("\nClearing old data...")
            clear_tables(session)

            print("\nInserting...")
            store_ids, item_ids, _ = seed(session, data)
        else:
            # Pull IDs from DB for --check mode
            store_ids = [r[0] for r in session.execute(
                text("SELECT id FROM stores ORDER BY id")).fetchall()]
            item_ids = [r[0] for r in session.execute(
                text("SELECT id FROM items ORDER BY id")).fetchall()]

        print("\nRunning verification queries...")
        verify_queries(session, store_ids, item_ids)
        print("\nPhase 1 done.")

    except Exception as e:
        session.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
