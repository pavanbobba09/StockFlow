"""
Initialize a fresh hosted StockFlow database before the API starts.

Local Docker Compose uses data/schema/init.sql at container creation time. Hosted
Postgres providers usually start as an empty database, so this script makes the
Docker image self-bootstrapping for free cloud deployments.
"""

from __future__ import annotations

from pathlib import Path
import time

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from agents.demo_simulator import ensure_demo_schema
from data.db import SessionLocal, engine
from data.seed import clear_tables, seed
from data.synthetic import build_seed_data


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = ROOT / "data" / "schema" / "init.sql"


def wait_for_database(attempts: int = 30, delay_seconds: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)


def apply_schema_if_needed() -> None:
    inspector = inspect(engine)
    if "stores" in inspector.get_table_names(schema="public"):
        return

    schema_sql = SCHEMA_SQL.read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(schema_sql)
        raw.commit()
    finally:
        raw.close()


def seed_if_needed() -> None:
    session = SessionLocal()
    try:
        store_count = session.execute(text("SELECT COUNT(*) FROM stores")).scalar() or 0
        item_count = session.execute(text("SELECT COUNT(*) FROM items")).scalar() or 0
        if store_count and item_count:
            ensure_demo_schema(session)
            return

        clear_tables(session)
        seed(session, build_seed_data(seed=42))
        ensure_demo_schema(session)
    finally:
        session.close()


def main() -> None:
    wait_for_database()
    apply_schema_if_needed()
    seed_if_needed()


if __name__ == "__main__":
    main()
