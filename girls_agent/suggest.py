"""Real places for a meetup: after the date poll, restaurants and events found by web search for that date.

The LLM picks from the search results only and returns JSON; a place whose link isn't in the results
is dropped, so nothing made up gets into the poll. Weather, the group's "не предлагай нам …" list
and past meetups are taken into account. Any failure returns [] and the poll falls back to the starter ideas.
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
SPARE = 2  # extra candidates asked for, since some get dropped by the checks below

MONTHS_NL = ["januari", "februari", "maart", "april", "mei", "juni",
             "juli", "augustus", "september", "oktober", "november", "december"]

PROMPT = (
    "Подбери места для встречи подруг {day} по результатам поиска ниже: до {n} вариантов, лучшие первыми, "
    "разных видов, если получится ({kinds}). Только то, что есть в результатах: ничего не выдумывай и не бери из памяти. "
    "Событие подходит, только если по результатам оно идёт именно {day}; ресторан или бар — если просто существует.\n"
    "Учитывай: погода — {weather} (в дождь и холод лучше в помещении); группа просила не предлагать: {exclusions} "
    "(такое не бери совсем); недавно уже были: {past} (не повторяй).\n"
    "Ответь только JSON-массивом без Markdown, элементы: "
    '{{"kind": один из {kinds}, "title": "название", "details": "одна фраза по-русски", '
    '"address": "адрес из результатов или пустая строка", "url": "ссылка из результатов"}}. '
    "У вечеринки и мероприятия title строго в виде «Название события — Площадка» («Glitterbal — Melkweg»), а details — что это: "
    "жанр, артисты или программа, время, если есть. Просто «вечеринка в клубе» не годится: если из результатов "
    "непонятно, что за событие, не бери его. У ресторана details — кухня и чем хорош. "
    "url — самая конкретная ссылка из результатов: страница этого события или места, а не общая афиша, если такая есть. "
    "Не нашлось подходящего — пустой массив [].\n\n"
    "Результаты поиска:\n{results}"
)


def queries(city: str, day: date) -> List[str]:
    """A restaurant search and an events search for that date; Dutch sites list the most."""
    when = f"{day.day} {MONTHS_NL[day.month - 1]} {day.year}"
    return [f"leuke restaurants {city} etentje met vriendinnen",
            f"uitgaan {city} {when} evenementen concerten feesten"]


def named_event(title: str, results: str) -> bool:
    """True for "Event — Venue" where the event's name is its own and appears in the search results."""
    parts = re.split(r"\s+[—–-]\s+", title, maxsplit=1)
    if len(parts) != 2:
        return False
    name, venue = (p.strip().lower() for p in parts)
    return bool(name) and name != venue and name in results.lower()


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
        kind = item.get("kind") if item.get("kind") in KINDS else "мероприятие"
        address = planner.truncate(str(item.get("address") or "").strip(), 120)
        if kind != "ресторан" and not named_event(title, results):
            continue  # just a venue: nobody can tell what the event is
        places.append({
            "kind": kind,
            "title": title,
            "details": planner.truncate(str(item.get("details") or "").strip(), 200),
            # just a city isn't an address: a map link to "Amsterdam" helps nobody
            "address": address if re.search(r"\d", address) else "",
            "url": url,
        })
    return places


def diverse(places: List[dict], n: int) -> List[dict]:
    """n places, one of each kind first (in the LLM's order), then the rest: the model tends to list restaurants only."""
    first = [p for i, p in enumerate(places) if p["kind"] not in {q["kind"] for q in places[:i]}]
    return (first + [p for p in places if p not in first])[:n]


async def real_places(cfg, now: datetime, day: date, n: int, exclusions: Sequence[str] = (),
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
        day=planner.format_date(day), n=n + SPARE, kinds=", ".join(KINDS), results=results,
        weather=await weather.lookup(cfg, None, day, now.date()),
        exclusions="; ".join(exclusions) or "ничего", past="; ".join(past) or "ничего",
    )
    messages = llm.build_messages(prompt, now, group=llm.load_group(cfg.group_file, cfg.weather_city), search=True)
    answer = await llm.complete(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_models, messages, timeout=LLM_TIMEOUT)
    places = diverse(parse(answer, results), n)
    if not places:
        log.info("no real places for %s: falling back to starter ideas", day)
    return places
