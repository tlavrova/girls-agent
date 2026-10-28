"""Morning message: a wish for the day, the top science or tech story and the weather, in 3 lines.

Weather comes from Open-Meteo (weather.py) and headlines from RSS feeds, both free and keyless.
The LLM writes the message once a day and it goes to every chat, so it costs one request.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List

import httpx

from . import llm, weather

log = logging.getLogger("girls_agent")

TIMEOUT = 15
# Nobody is waiting for the morning message, so give slow free-tier models time to answer
LLM_TIMEOUT = 90

PROMPT = (
    "Напиши утреннее сообщение для чата подруг: ровно три строки (здесь можно длиннее обычного), без Markdown:\n"
    "1. Доброе утро и короткое пожелание на день, в твоём стиле, без банальщины.\n"
    "2. «🔬 » и самая интересная новость науки или технологий из списка ниже, одной-двумя фразами по-русски: "
    "открытие, космос, медицина, гаджеты, ИИ — то, о чём захочется рассказать подруге. "
    "Не обязательно сегодняшняя. Местные британские новости (погода, муниципалитеты, переработка мусора) не бери.\n"
    "3. «{emoji} {city}: » и погода из данных ниже одной строкой, можно с короткой практичной ремаркой "
    "(зонт, шарф, тёмные очки).\n\n"
    "Погода на сегодня: {weather}\n\n"
    "Новости:\n{news}"
)


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
        today = await weather.forecast(client, cfg.weather_lat, cfg.weather_lon, str(cfg.tz), now.date())
        news = [h for url in cfg.news_feeds for h in await fetch_headlines(client, url)]
    emoji, facts = today or ("🌡", "нет данных")

    answer = ""
    if cfg.llm_api_key and news:
        prompt = PROMPT.format(emoji=emoji, city=cfg.weather_city, weather=facts,
                               news="\n".join(f"- {h}" for h in news))
        messages = llm.build_messages(prompt, now, group=llm.load_group(cfg.group_file, cfg.weather_city))
        answer = await llm.complete(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_models, messages,
                                    timeout=LLM_TIMEOUT)
    if answer:
        return answer

    lines = ["Доброе утро, девочки ☀️ Пусть день будет добрым."]
    if today:
        lines.append(f"{emoji} {cfg.weather_city}: {facts}.")
    return "\n".join(lines)
