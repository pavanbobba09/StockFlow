"""
Synthetic data generator for StockFlow.

Produces realistic-but-fake demand histories with:
- Per-store base demand levels (stores differ from each other)
- Weekly seasonality (weekends spike for most items)
- Gaussian noise
- Occasional event spikes (stadium, holiday, promotion)
- Perishable items with varied shelf lives
"""

import random
import math
from datetime import date, timedelta, time
from typing import List, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Store and warehouse fixtures (US metro area bounding box)
# ---------------------------------------------------------------------------

STORES_RAW = [
    ("Downtown Central",    40.7128, -74.0060),
    ("Midtown North",       40.7549, -73.9840),
    ("Brooklyn Heights",    40.6960, -73.9937),
    ("Queens Village",      40.7282, -73.7949),
    ("Bronx River",         40.8448, -73.8648),
    ("Hoboken Junction",    40.7440, -74.0324),
    ("Jersey City West",    40.7178, -74.0431),
    ("Staten Island Ferry", 40.6445, -74.0739),
    ("Upper East Side",     40.7736, -73.9566),
    ("Lower East Side",     40.7157, -73.9863),
    ("Williamsburg",        40.7081, -73.9571),
    ("Astoria",             40.7721, -73.9302),
    ("Flushing Main",       40.7675, -73.8330),
    ("Forest Hills",        40.7197, -73.8446),
    ("Bay Ridge",           40.6351, -74.0243),
]

WAREHOUSES_RAW = [
    ("Newark Distribution Center", 40.7357, -74.1724),
    ("Long Island City Hub",       40.7447, -73.9485),
    ("Bronx Logistics",            40.8500, -73.9000),
]

ITEMS_RAW = [
    # (name, shelf_life_days, base_daily_demand_range)
    ("Whole Milk (1gal)",       7,  (20, 60)),
    ("Bread Loaf",              4,  (15, 45)),
    ("Eggs (12ct)",             21, (10, 35)),
    ("Chicken Breast (1lb)",    3,  (8,  30)),
    ("Bananas (bunch)",         5,  (12, 40)),
    ("Greek Yogurt",            14, (6,  25)),
    ("Orange Juice (64oz)",     10, (8,  28)),
    ("Ground Beef (1lb)",       3,  (5,  20)),
    ("Butter (1lb)",            30, (4,  15)),
    ("Cheddar Cheese (8oz)",    45, (5,  18)),
    ("Baby Spinach (5oz)",      5,  (4,  16)),
    ("Strawberries (1lb)",      4,  (6,  22)),
    ("Salmon Fillet (1lb)",     2,  (3,  12)),
    ("Cream Cheese (8oz)",      21, (4,  14)),
    ("Sourdough Loaf",          3,  (5,  20)),
]

# Delivery weekdays per store: each store gets 2 delivery days
DELIVERY_PATTERNS = [
    [0, 3],  # Mon, Thu
    [1, 4],  # Tue, Fri
    [2, 5],  # Wed, Sat
    [0, 4],  # Mon, Fri
    [1, 5],  # Tue, Sat
]

HISTORY_DAYS = 180


# ---------------------------------------------------------------------------
# Demand generation
# ---------------------------------------------------------------------------

def _weekly_factor(day_of_week: int, item_idx: int) -> float:
    """Weekend boost varies by item category."""
    weekend = day_of_week >= 5  # Sat/Sun
    if item_idx in (0, 1, 4, 5, 6):   # staples: moderate weekend bump
        return 1.25 if weekend else 1.0
    if item_idx in (7, 11, 12):        # proteins/fresh: stronger weekend
        return 1.45 if weekend else 1.0
    return 1.1 if weekend else 1.0


def _event_spike(d: date, store_idx: int, rng: random.Random) -> float:
    """Occasional multiplicative spike: holiday, local event, promo."""
    # Fixed holidays (simplified)
    holidays = {(11, 27), (12, 24), (12, 25), (1, 1), (7, 4), (11, 28)}
    if (d.month, d.day) in holidays:
        return rng.uniform(1.5, 2.2)
    # Random store-specific event ~3% of days
    seed = store_idx * 10000 + d.toordinal()
    local_rng = random.Random(seed)
    if local_rng.random() < 0.03:
        return local_rng.uniform(1.3, 1.8)
    return 1.0


def generate_demand_series(
    store_idx: int,
    item_idx: int,
    base_range: Tuple[int, int],
    history_days: int,
    end_date: date,
    rng: random.Random,
) -> List[Tuple[date, int]]:
    """Return list of (date, quantity) for one store-item pair."""
    base = rng.uniform(*base_range)
    # Each store has a multiplier so stores differ
    store_multiplier = rng.uniform(0.6, 1.4)
    base *= store_multiplier

    rows = []
    for i in range(history_days):
        d = end_date - timedelta(days=history_days - 1 - i)
        weekly = _weekly_factor(d.weekday(), item_idx)
        event = _event_spike(d, store_idx, rng)
        noise = rng.gauss(1.0, 0.12)
        qty = max(0, int(round(base * weekly * event * noise)))
        rows.append((d, qty))
    return rows


# ---------------------------------------------------------------------------
# Par levels and initial inventory
# ---------------------------------------------------------------------------

def par_level(base_range: Tuple[int, int], delivery_freq_days: int) -> int:
    """Par = average daily demand × days between deliveries × safety factor."""
    avg = sum(base_range) / 2
    return max(1, int(avg * delivery_freq_days * 1.3))


def initial_inventory(par: int, rng: random.Random) -> int:
    """Start stores somewhere between 40% and 90% of par."""
    return int(par * rng.uniform(0.4, 0.9))


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_seed_data(seed: int = 42) -> dict:
    """
    Return a dict with all synthetic data ready to insert:
      stores, warehouses, items,
      delivery_schedules, demand_history, inventory
    """
    rng = random.Random(seed)
    np.random.seed(seed)

    today = date.today()

    # Stores
    stores = []
    for i, (name, lat, lng) in enumerate(STORES_RAW):
        # tiny jitter so nearby-store queries are interesting
        stores.append({
            "name": name,
            "lat": lat + rng.uniform(-0.005, 0.005),
            "lng": lng + rng.uniform(-0.005, 0.005),
        })

    # Warehouses
    warehouses = [
        {"name": name, "lat": lat, "lng": lng}
        for name, lat, lng in WAREHOUSES_RAW
    ]

    # Items
    items = [
        {"name": name, "shelf_life_days": shelf}
        for name, shelf, _ in ITEMS_RAW
    ]

    # Delivery schedules (2 delivery days per store)
    delivery_schedules = []
    for store_idx in range(len(stores)):
        pattern = DELIVERY_PATTERNS[store_idx % len(DELIVERY_PATTERNS)]
        for weekday in pattern:
            delivery_schedules.append({
                "store_idx": store_idx,   # resolved to store_id after insert
                "weekday": weekday,
                "cutoff_time": time(8, 0),
            })

    # Demand history + par levels
    demand_history = []
    par_levels = {}  # (store_idx, item_idx) -> par
    for store_idx in range(len(stores)):
        pattern = DELIVERY_PATTERNS[store_idx % len(DELIVERY_PATTERNS)]
        delivery_gap = 7 // len(pattern)  # approx days between deliveries
        for item_idx, (_, shelf, base_range) in enumerate(ITEMS_RAW):
            series = generate_demand_series(
                store_idx, item_idx, base_range, HISTORY_DAYS, today, rng
            )
            for d, qty in series:
                demand_history.append({
                    "store_idx": store_idx,
                    "item_idx": item_idx,
                    "date": d,
                    "quantity": qty,
                })
            par_levels[(store_idx, item_idx)] = par_level(base_range, delivery_gap)

    # Current inventory for stores
    inventory = []
    for store_idx in range(len(stores)):
        for item_idx, (_, shelf, _) in enumerate(ITEMS_RAW):
            par = par_levels[(store_idx, item_idx)]
            qty = initial_inventory(par, rng)
            # expiry: random within shelf life window
            expiry = today + timedelta(days=rng.randint(1, max(1, shelf)))
            inventory.append({
                "location_idx": store_idx,
                "location_type": "store",
                "item_idx": item_idx,
                "quantity": qty,
                "expiry_date": expiry,
            })

    # Warehouse inventory (larger quantities, longer shelf)
    for wh_idx in range(len(warehouses)):
        for item_idx, (_, shelf, base_range) in enumerate(ITEMS_RAW):
            qty = int(sum(base_range) / 2 * len(stores) * 0.5)
            expiry = today + timedelta(days=max(1, shelf))
            inventory.append({
                "location_idx": wh_idx,
                "location_type": "warehouse",
                "item_idx": item_idx,
                "quantity": qty,
                "expiry_date": expiry,
            })

    return {
        "stores": stores,
        "warehouses": warehouses,
        "items": items,
        "delivery_schedules": delivery_schedules,
        "demand_history": demand_history,
        "inventory": inventory,
        "par_levels": par_levels,
    }
