"""Weather from Open-Meteo: free, keyless, forecasts up to 16 days ahead."""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import httpx

log = logging.getLogger("girls_agent")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT = 15
MAX_DAYS_AHEAD = 15

# WMO weather codes used by Open-Meteo, grouped: (highest code in the group, emoji, Russian description)
WEATHER_CODES = [
    (0, "☀️", "ясно"),
    (2, "🌤", "переменная облачность"),
    (3, "☁️", "пасмурно"),
    (48, "🌫", "туман"),
    (57, "🌦", "морось"),
    (67, "🌧", "дождь"),
    (77, "❄️", "снег"),
    (82, "🌦", "ливни"),
    (86, "🌨", "снегопад"),
    (99, "⛈", "гроза"),
]

DAILY_FIELDS = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max"


def describe(code: int) -> tuple:
    """(emoji, description) for a WMO weather code."""
    for top, emoji, text in WEATHER_CODES:
        if code <= top:
            return emoji, text
    return "🌡", "погода"


def summary(daily: dict, i: int = 0) -> tuple:
    """(emoji, facts) for day i of Open-Meteo's `daily` block."""
    emoji, text = describe(int(daily["weather_code"][i]))
    facts = (
        f"{text}, от {round(daily['temperature_2m_min'][i])}° до {round(daily['temperature_2m_max'][i])}°, "
        f"вероятность осадков {daily['precipitation_probability_max'][i]}%, "
        f"ветер до {round(daily['wind_speed_10m_max'][i])} км/ч"
    )
    return emoji, facts


async def geocode(client: httpx.AsyncClient, city: str) -> Optional[tuple]:
    """(lat, lon, name in Russian) of the best match for a city name, or None."""
    resp = await client.get(GEOCODING_URL, params={"name": city, "count": 1, "language": "ru"})
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    best = results[0]
    return best["latitude"], best["longitude"], best.get("name", city)


async def forecast(client: httpx.AsyncClient, lat: float, lon: float, tz: str, day: date) -> Optional[tuple]:
    """(emoji, facts) for one day, or None if the request failed."""
    try:
        resp = await client.get(FORECAST_URL, params={
            "latitude": lat, "longitude": lon, "timezone": tz, "daily": DAILY_FIELDS,
            "start_date": day.isoformat(), "end_date": day.isoformat(),
        })
        resp.raise_for_status()
        return summary(resp.json()["daily"])
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        log.warning("weather request failed: %s %s", type(e).__name__, e)
        return None


async def lookup(cfg, city: Optional[str], day: date, today: date) -> str:
    """Weather facts for the assistant, or a short explanation of why there are none."""
    if not 0 <= (day - today).days <= MAX_DAYS_AHEAD:
        return f"Прогноз есть только на ближайшие {MAX_DAYS_AHEAD} дней."
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        if city:
            try:
                place = await geocode(client, city)
            except (httpx.HTTPError, KeyError, ValueError) as e:
                log.warning("geocoding failed: %s %s", type(e).__name__, e)
                return "Сервис погоды сейчас недоступен."
            if place is None:
                return f"Не нашла город «{city}»."
            lat, lon, name = place
        else:
            lat, lon, name = cfg.weather_lat, cfg.weather_lon, cfg.weather_city
        result = await forecast(client, lat, lon, str(cfg.tz), day)
    if result is None:
        return "Сервис погоды сейчас недоступен."
    emoji, facts = result
    return f"{emoji} {name}, {day.isoformat()}: {facts}"
