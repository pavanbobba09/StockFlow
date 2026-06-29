"""
Free live-signal integrations for StockFlow.

These adapters use no-key public APIs and are intentionally fail-soft. If a
remote API is unavailable, the agent engine keeps running with cached/default
signals instead of blocking supply-chain decisions.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import text
from sqlalchemy.orm import Session


NWS_BASE_URL = "https://api.weather.gov"
HOLIDAY_BASE_URL = "https://date.nager.at/api/v3"
DEFAULT_USER_AGENT = "StockFlow/1.0 (portfolio-demo@example.com)"


def get_live_signal_summary(db: Session, force_refresh: bool = False) -> dict[str, Any]:
    """
    Return live demand signals from free public APIs.

    The returned `demand_multiplier` is applied to the simulation scenario.
    """
    if os.getenv("LIVE_SIGNALS_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return _default_summary("disabled")

    cached = _read_cached_summary(db)
    if cached and not force_refresh:
        return cached

    try:
        stores = _stores_for_live_signals(db)
        weather_signals = [_fetch_nws_store_weather(store) for store in stores]
        holiday_signal = _fetch_us_holiday_signal()
        summary = _summarize_signals(weather_signals, holiday_signal)
        _write_cached_summary(db, summary)
        return summary
    except Exception as exc:
        fallback = cached or _default_summary("api_unavailable")
        fallback["status"] = "stale" if cached else "fallback"
        fallback["error"] = str(exc)
        return fallback


def _stores_for_live_signals(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text("SELECT id, name, lat, lng FROM stores ORDER BY id LIMIT 5")
    ).fetchall()
    return [{"id": r.id, "name": r.name, "lat": float(r.lat), "lng": float(r.lng)} for r in rows]


def _fetch_nws_store_weather(store: dict[str, Any]) -> dict[str, Any]:
    point = _http_json(f"{NWS_BASE_URL}/points/{store['lat']:.4f},{store['lng']:.4f}")
    hourly_url = point.get("properties", {}).get("forecastHourly")
    if not hourly_url:
        raise ValueError(f"NWS did not return hourly forecast for {store['name']}")

    forecast = _http_json(hourly_url)
    period = (forecast.get("properties", {}).get("periods") or [{}])[0]
    short = str(period.get("shortForecast") or "Unknown")
    precip = period.get("probabilityOfPrecipitation", {}).get("value")
    temp = period.get("temperature")
    wind = str(period.get("windSpeed") or "")
    weather_multiplier = _weather_multiplier(short, precip, temp, wind)
    return {
        "provider": "National Weather Service",
        "store_id": store["id"],
        "store_name": store["name"],
        "summary": short,
        "temperature": temp,
        "precip_probability": precip,
        "wind_speed": wind,
        "demand_multiplier": weather_multiplier,
    }


def _fetch_us_holiday_signal() -> dict[str, Any]:
    today = date.today()
    holidays = []
    for year in {today.year, (today + timedelta(days=10)).year}:
        holidays.extend(_http_json(f"{HOLIDAY_BASE_URL}/PublicHolidays/{year}/US"))
    upcoming = []
    for holiday in holidays:
        holiday_date = date.fromisoformat(holiday["date"])
        days_until = (holiday_date - today).days
        if 0 <= days_until <= 7:
            upcoming.append(
                {
                    "name": holiday.get("localName") or holiday.get("name"),
                    "date": holiday["date"],
                    "days_until": days_until,
                }
            )
    multiplier = 1.0
    if upcoming:
        multiplier = 1.18 if min(h["days_until"] for h in upcoming) <= 2 else 1.08
    return {
        "provider": "Nager.Date",
        "upcoming_holidays": upcoming,
        "demand_multiplier": multiplier,
    }


def _summarize_signals(
    weather_signals: list[dict[str, Any]],
    holiday_signal: dict[str, Any],
) -> dict[str, Any]:
    weather_multiplier = max([s["demand_multiplier"] for s in weather_signals] or [1.0])
    holiday_multiplier = float(holiday_signal.get("demand_multiplier", 1.0))
    demand_multiplier = round(min(1.55, weather_multiplier * holiday_multiplier), 3)
    reasons = []
    if weather_multiplier > 1.0:
        strongest = max(weather_signals, key=lambda s: s["demand_multiplier"])
        reasons.append(
            f"{strongest['store_name']} weather signal is {strongest['summary']} "
            f"({strongest['demand_multiplier']:.2f}x demand pressure)."
        )
    if holiday_multiplier > 1.0:
        names = ", ".join(h["name"] for h in holiday_signal.get("upcoming_holidays", [])[:2])
        reasons.append(f"Upcoming U.S. holiday demand pressure: {names}.")
    if not reasons:
        reasons.append("No major free live-signal demand pressure detected.")

    return {
        "status": "live",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "demand_multiplier": demand_multiplier,
        "providers": ["National Weather Service", "Nager.Date"],
        "reasons": reasons,
        "weather": weather_signals,
        "holidays": holiday_signal,
    }


def _weather_multiplier(
    short_forecast: str,
    precip_probability: int | None,
    temperature: int | None,
    wind_speed: str,
) -> float:
    text_value = f"{short_forecast} {wind_speed}".lower()
    multiplier = 1.0
    if any(term in text_value for term in ["rain", "storm", "snow", "sleet", "thunder"]):
        multiplier += 0.16
    if precip_probability is not None and precip_probability >= 55:
        multiplier += 0.12
    if temperature is not None and temperature >= 90:
        multiplier += 0.08
    if temperature is not None and temperature <= 32:
        multiplier += 0.08
    if "wind" in text_value and any(token in text_value for token in ["20", "25", "30", "35"]):
        multiplier += 0.05
    return round(min(multiplier, 1.35), 3)


def _http_json(url: str) -> Any:
    timeout = float(os.getenv("LIVE_API_TIMEOUT_SECONDS", "3.0"))
    req = Request(
        url,
        headers={
            "Accept": "application/geo+json, application/json",
            "User-Agent": os.getenv("NWS_USER_AGENT", DEFAULT_USER_AGENT),
        },
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"live API request failed for {url}: {exc}") from exc


def _read_cached_summary(db: Session) -> dict[str, Any] | None:
    raw = db.execute(
        text("SELECT value FROM demo_state WHERE key = 'live_signal_summary'")
    ).scalar()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    generated_at = payload.get("generated_at")
    if not generated_at:
        return None
    generated = datetime.fromisoformat(generated_at.replace("Z", ""))
    max_age = int(os.getenv("LIVE_SIGNAL_CACHE_SECONDS", "1800"))
    if datetime.utcnow() - generated > timedelta(seconds=max_age):
        return None
    payload["status"] = "cached"
    return payload


def _write_cached_summary(db: Session, summary: dict[str, Any]) -> None:
    db.execute(
        text("""
            INSERT INTO demo_state (key, value, updated_at)
            VALUES ('live_signal_summary', :value, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
        """),
        {"value": json.dumps(summary)},
    )


def _default_summary(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "demand_multiplier": 1.0,
        "providers": ["National Weather Service", "Nager.Date"],
        "reasons": ["Live signals are not currently affecting demand."],
        "weather": [],
        "holidays": {"provider": "Nager.Date", "upcoming_holidays": [], "demand_multiplier": 1.0},
    }
