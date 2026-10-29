"""Real places for a meetup: after the date poll, restaurants and events found by web search for that date.

The LLM picks from the search results only and returns JSON; a place whose link isn't in the results
is dropped, so nothing made up gets into the poll. Weather, what people dislike and past meetups
are taken into account. Any failure returns [] and the poll falls back to the starter ideas.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import List, Sequence

from . import llm, planner, search, weather

log = logging.getLogger("girls_agent")

# Nobody is waiting for it, so give slow free-tier models time to answer
LLM_TIMEOUT = 90
KINDS = ("ресторан", "вечеринка", "мероприятие")

MONTHS_NL = ["januari", "februari", "maart", "april", "mei", "juni",
             "juli", "augustus", "september", "oktober", "november", "december"]

PROMPT = (
    "Подбери места для встречи подруг {day} по результатам поиска ниже: ровно {n} варианта, разных видов, "
    "если получится ({kinds}). Только то, что есть в результатах: ничего не выдумывай и не бери из памяти. "
    "Событие подходит, только если по результатам оно идёт именно {day}; ресторан или бар — если просто существует.\n"
    "Учитывай: погода — {weather} (в дождь и холод лучше в помещении); что о себе рассказывали подруги: {facts}; "
    "недавно уже были: {past} (не повторяй).\n"
    "Ответь только JSON-массивом без Markdown, элементы: "
    '{{"kind": один из {kinds}, "title": "название", "details": "одна фраза по-русски, что там и почему подходит", '
    '"address": "адрес из результатов или пустая строка", "url": "ссылка из результатов"}}. '
    "Не нашлось подходящего — пустой массив [].\n\n"
    "Результаты поиска:\n{results}"
)


def queries(city: str, day: date) -> List[str]:
    """A restaurant search and an events search for that date; Dutch sites list the most."""
    when = f"{day.day} {MONTHS_NL[day.month - 1]} {day.year}"
    return [f"leuke restaurants {city} etentje met vriendinnen",
            f"uitgaan {city} {when} evenementen concerten feesten"]


def parse(answer: str, results: str) -> List[dict]:
    """Places from the LLM's JSON; ones without a link that appears in the search results are dropped."""
    match = re.search(r"\[.*\]", answer, re.DOTALL)
    try:
        items = json.loads(match.group(0)) if match else []
    except ValueError:
        return []
    places = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        title = planner.truncate(str(item.get("title") or "").strip(), 80)
        url = str(item.get("url") or "").strip()
        if not title or not url.startswith("http") or url not in results:
            continue
        places.append({
            "kind": item.get("kind") if item.get("kind") in KINDS else "мероприятие",
            "title": title,
            "details": planner.truncate(str(item.get("details") or "").strip(), 200),
            "address": planner.truncate(str(item.get("address") or "").strip(), 120),
            "url": url,
        })
    return places


async def real_places(cfg, now: datetime, day: date, n: int, facts: Sequence[str] = (),
                      past: Sequence[str] = ()) -> List[dict]:
    """Up to n real places for that day, or [] when search or the LLM is unavailable or nothing fits."""
    if n <= 0 or not (cfg.llm_api_key and cfg.search_api_key):
        return []
    found = []
    for query in queries(cfg.meetup_city, day):
        text = await search.lookup(cfg.search_api_key, query, now.date(), cfg.search_daily_limit)
        if "http" in text:  # otherwise it's a note like "nothing found" or "search is down"
            found.append(text)
    if not found:
        return []
    results = "\n\n".join(found)
    prompt = PROMPT.format(
        day=planner.format_date(day), n=n, kinds=", ".join(KINDS), results=results,
        weather=await weather.lookup(cfg, None, day, now.date()),
        facts="; ".join(facts) or "ничего", past="; ".join(past) or "ничего",
    )
    messages = llm.build_messages(prompt, now, group=llm.load_group(cfg.group_file, cfg.weather_city), search=True)
    answer = await llm.complete(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_models, messages, timeout=LLM_TIMEOUT)
    places = parse(answer, results)[:n]
    if not places:
        log.info("no real places for %s: falling back to starter ideas", day)
    return places
