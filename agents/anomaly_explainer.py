"""
Anomaly explainer agent — explains forecast jumps and chronic issues.

Triggered by:
  - Forecast delta beyond threshold (e.g. >50% change)
  - Chronic stockouts (3+ in 7 days for same store-item)
  - Chronic waste (high expiry rate)

Output: plain-language explanation surfaced to humans.
"""

from datetime import date, timedelta
from typing import List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_anthropic import ChatAnthropic


SYSTEM_PROMPT = """\
You are an anomaly explainer for a food chain inventory system.

Your job: explain why a forecast changed dramatically or why a store-item keeps stocking out or wasting stock.

Given:
- Historical demand data
- Recent forecast predictions
- Event context (holidays, weather, local events if available)
- Store and item details

Produce a clear, concise explanation (2-3 sentences max) of what likely caused the anomaly.

Examples:
- "Forecast jumped 60% because Thanksgiving is next week. Historical data shows 2x spike for turkey and stuffing around this holiday."
- "Store 5 has stocked out 4 times this week on milk. Delivery schedule is Mon/Thu but weekend demand spikes are not covered. Consider adding a Saturday delivery."
- "High waste on strawberries at Store 3. Shelf life is 4 days but deliveries are 7 days apart. Reduce order quantity or increase delivery frequency."

Be specific. Reference numbers. Suggest fixes if obvious.
"""


def explain_forecast_jump(
    store_id: int,
    item_id: int,
    old_forecast: float,
    new_forecast: float,
    context: dict,
) -> str:
    """
    Explain a sudden forecast change.

    Args:
        store_id: Store ID
        item_id: Item ID
        old_forecast: Previous forecast value
        new_forecast: New forecast value
        context: dict with keys:
            - item_name: str
            - store_name: str
            - recent_demand: List[int] (last 7-14 days)
            - upcoming_events: Optional[str] (holiday, promo, etc.)
            - shelf_life_days: int

    Returns:
        Plain-language explanation (2-3 sentences)
    """
    pct_change = (new_forecast - old_forecast) / old_forecast * 100 if old_forecast > 0 else 0

    prompt = f"""
Forecast for {context.get('item_name', 'item')} at {context.get('store_name', 'store')} jumped {pct_change:.0f}% (from {old_forecast:.0f} to {new_forecast:.0f} units/day).

Recent demand (last 7 days): {context.get('recent_demand', [])}
Upcoming events: {context.get('upcoming_events', 'none noted')}
Shelf life: {context.get('shelf_life_days', 'unknown')} days

Why did the forecast change so much? What should we watch for?
""".strip()

    llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0)
    response = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    return response.content.strip()


def explain_chronic_stockouts(
    store_id: int,
    item_id: int,
    stockout_dates: List[date],
    context: dict,
) -> str:
    """
    Explain chronic stockouts (3+ in 7 days).

    Args:
        store_id: Store ID
        item_id: Item ID
        stockout_dates: List of dates with stockouts
        context: dict with keys:
            - item_name: str
            - store_name: str
            - delivery_weekdays: List[int] (0=Mon)
            - avg_daily_demand: float
            - par_level: int

    Returns:
        Explanation + suggested fix
    """
    days = ", ".join(d.strftime("%a %m/%d") for d in stockout_dates[-5:])
    delivery_days = ", ".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d] for d in context.get("delivery_weekdays", []))

    prompt = f"""
{context.get('item_name', 'Item')} at {context.get('store_name', 'store')} stocked out {len(stockout_dates)} times recently: {days}.

Delivery schedule: {delivery_days if delivery_days else 'unknown'}
Average daily demand: {context.get('avg_daily_demand', 'unknown')} units
Current par level: {context.get('par_level', 'unknown')}

Why does this keep happening? What should we change?
""".strip()

    llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0)
    response = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    return response.content.strip()


def explain_chronic_waste(
    store_id: int,
    item_id: int,
    waste_units_7d: int,
    ordered_units_7d: int,
    context: dict,
) -> str:
    """
    Explain high waste rate.

    Args:
        store_id: Store ID
        item_id: Item ID
        waste_units_7d: Units wasted in last 7 days
        ordered_units_7d: Units ordered in last 7 days
        context: dict with keys:
            - item_name: str
            - store_name: str
            - shelf_life_days: int
            - delivery_weekdays: List[int]
            - avg_daily_demand: float

    Returns:
        Explanation + suggested fix
    """
    waste_rate = waste_units_7d / ordered_units_7d if ordered_units_7d > 0 else 0

    prompt = f"""
{context.get('item_name', 'Item')} at {context.get('store_name', 'store')} wasted {waste_units_7d} units in the last 7 days ({waste_rate*100:.0f}% of orders).

Shelf life: {context.get('shelf_life_days', 'unknown')} days
Delivery schedule: {", ".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d] for d in context.get("delivery_weekdays", []))}
Average daily demand: {context.get('avg_daily_demand', 'unknown')} units

Why is waste so high? What should we adjust?
""".strip()

    llm = ChatAnthropic(model="claude-haiku-4-5-20251001", temperature=0)
    response = llm.invoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    return response.content.strip()
