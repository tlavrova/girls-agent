"""Kids' weekend picks on Fridays: 3 real family events or outings for the coming weekend.

Events come from web search (search.py) and the LLM picks from the results only, checking they fall
on that weekend. Composed once for all chats. Nothing is sent when nothing suitable was found:
an empty or made-up message is worse than none.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import llm, planner, search

# Nobody is waiting for it, so give slow free-tier models time to answer
LLM_TIMEOUT = 90
NOTHING = "НИЧЕГО"

MONTHS_NL = ["januari", "februari", "maart", "april", "mei", "juni",
             "juli", "augustus", "september", "oktober", "november", "december"]

PROMPT = (
    "Напиши для чата подруг подборку, куда сходить с детьми в эти выходные ({saturday} и {sunday}), "
    "по результатам поиска ниже. Без Markdown, можно длиннее обычного.\n"
    "Первая строка: «👧 Куда с детьми на выходных». Дальше до трёх вариантов, между ними пустая строка, у каждого:\n"
    "эмодзи и название — одна фраза, что там и для какого возраста;\n"
    "когда (день и время) и где (место, город);\n"
    "ссылка из результатов, голым URL.\n"
    "Бери только то, что по результатам точно идёт в эти выходные или открыто в эти дни: спектакли, музеи "
    "с детскими программами, мастер-классы, фестивали, фермы, катки. Ничего не добавляй от себя и не выдумывай "
    "даты, адреса и цены. Подходят меньше трёх — дай меньше. Не подходит ничего — ответь одним словом {nothing}.\n\n"
    "Результаты поиска:\n{results}"
)


def weekend(today: date) -> tuple:
    """The coming Saturday and Sunday (this weekend if today is Saturday or Sunday)."""
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    if today.weekday() == 6:
        saturday = today - timedelta(days=1)
    return saturday, saturday + timedelta(days=1)


def queries(city: str, saturday: date) -> list:
    """Two searches: Dutch sites list most local family events, the English one catches the expat ones."""
    month = MONTHS_NL[saturday.month - 1]
    return [f"uitjes met kinderen {city} weekend {saturday.day} {month} {saturday.year}",
            f"kids family events {city} {saturday:%B} {saturday.day} {saturday.year}"]


async def compose(cfg, now: datetime) -> str:
    """This weekend's picks, or "" when search or the LLM is unavailable or nothing suitable was found."""
    if not (cfg.llm_api_key and cfg.search_api_key):
        return ""
    saturday, sunday = weekend(now.date())
    results = []
    for query in queries(cfg.kids_picks_city, saturday):
        found = await search.lookup(cfg.search_api_key, query, now.date(), cfg.search_daily_limit)
        if "http" in found:  # otherwise it's a note like "nothing found" or "search is down"
            results.append(found)
    if not results:
        return ""
    prompt = PROMPT.format(saturday=planner.format_date(saturday), sunday=planner.format_date(sunday),
                           nothing=NOTHING, results="\n\n".join(results))
    messages = llm.build_messages(prompt, now, group=llm.load_group(cfg.group_file, cfg.weather_city), search=True)
    answer = await llm.complete(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_models, messages, timeout=LLM_TIMEOUT)
    return "" if NOTHING in answer.upper() else answer
