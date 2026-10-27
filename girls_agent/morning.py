"""Morning message: a wish for the day, the top world news and the weather, in 3-4 lines.

Weather comes from Open-Meteo and headlines from an RSS feed, both free and keyless.
The LLM writes the message once a day and it goes to every chat, so it costs one request.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Optional

import httpx

from . import llm

log = logging.getLogger("girls_agent")

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 15
# Nobody is waiting for the morning message, so give slow free-tier models time to answer
LLM_TIMEOUT = 90

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

PROMPT = (
    "Напиши утреннее сообщение для чата подруг: ровно три строки (здесь можно длиннее обычного), без Markdown:\n"
    "1. Доброе утро и короткое пожелание на день, в твоём стиле, без банальщины.\n"
    "2. «🌍 » и главная мировая новость дня из списка ниже, одной фразой по-русски. "
    "Выбери самую важную; войны и трагедии пересказывай нейтрально, без шуток.\n"
    "3. «{emoji} {city}: » и погода из данных ниже одной строкой, можно с короткой практичной ремаркой "
    "(зонт, шарф, тёмные очки).\n\n"
    "Погода на сегодня: {weather}\n\n"
    "Новости:\n{news}"
)


def describe_weather(code: int) -> tuple:
    """(emoji, description) for a WMO weather code."""
    for top, emoji, text in WEATHER_CODES:
        if code <= top:
            return emoji, text
    return "🌡", "погода"


def weather_summary(daily: dict) -> tuple:
    """(emoji, facts) from Open-Meteo's `daily` block for one day."""
    emoji, text = describe_weather(int(daily["weather_code"][0]))
    facts = (
        f"{text}, от {round(daily['temperature_2m_min'][0])}° до {round(daily['temperature_2m_max'][0])}°, "
        f"вероятность осадков {daily['precipitation_probability_max'][0]}%, "
        f"ветер до {round(daily['wind_speed_10m_max'][0])} км/ч"
    )
    return emoji, facts


def parse_headlines(rss: bytes, limit: int = 10) -> List[str]:
    """'Title: description' for the first items of an RSS feed."""
    items = []
    for item in ET.fromstring(rss).iter("item"):
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        if title:
            items.append(f"{title}: {description}" if description else title)
        if len(items) >= limit:
            break
    return items


async def fetch_weather(client: httpx.AsyncClient, lat: float, lon: float, tz: str) -> Optional[tuple]:
    try:
        resp = await client.get(WEATHER_URL, params={
            "latitude": lat, "longitude": lon, "timezone": tz, "forecast_days": 1,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                     "precipitation_probability_max,wind_speed_10m_max",
        })
        resp.raise_for_status()
        return weather_summary(resp.json()["daily"])
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        log.warning("weather request failed: %s %s", type(e).__name__, e)
        return None


async def fetch_headlines(client: httpx.AsyncClient, url: str) -> List[str]:
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        return parse_headlines(resp.content)
    except (httpx.HTTPError, ET.ParseError) as e:
        log.warning("news feed failed: %s %s", type(e).__name__, e)
        return []


async def compose(cfg, now: datetime) -> str:
    """Today's morning message; falls back to a plain greeting with the weather if the LLM is unavailable."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        weather = await fetch_weather(client, cfg.weather_lat, cfg.weather_lon, str(cfg.tz))
        news = await fetch_headlines(client, cfg.news_feed)
    emoji, facts = weather or ("🌡", "нет данных")

    answer = ""
    if cfg.llm_api_key and news:
        prompt = PROMPT.format(emoji=emoji, city=cfg.weather_city, weather=facts,
                               news="\n".join(f"- {h}" for h in news))
        messages = llm.build_messages(prompt, now)
        answer = await llm.complete(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_models, messages,
                                    timeout=LLM_TIMEOUT)
    if answer:
        return answer

    lines = ["Доброе утро, девочки ☀️ Пусть день будет добрым."]
    if weather:
        lines.append(f"{emoji} {cfg.weather_city}: {facts}.")
    return "\n".join(lines)
