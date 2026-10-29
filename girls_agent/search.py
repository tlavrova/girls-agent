"""Web search via Tavily: real places and events with links, so Gigi doesn't answer from the model's memory.

Free tier: 1,000 searches a month, so searches are capped per day.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Dict, Optional

import httpx

log = logging.getLogger("girls_agent")

SEARCH_URL = "https://api.tavily.com/search"
TIMEOUT = 20
MAX_RESULTS = 5
SNIPPET = 600  # characters of page text per result: enough for an address and hours, cheap in tokens

_used: Dict[date, int] = {}  # searches made per day, kept in memory: a restart simply resets it


def format_results(data: dict) -> str:
    """Numbered results for the LLM: title, link, then a snippet of the page."""
    lines = []
    for i, r in enumerate(data.get("results") or [], 1):
        text = " ".join(str(r.get("content") or "").split())[:SNIPPET]
        lines.append(f"{i}. {r.get('title') or ''} — {r.get('url') or ''}\n{text}")
    return "\n\n".join(lines)


async def lookup(api_key: Optional[str], query: str, today: date, daily_limit: int,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> str:
    """Search results as text for the LLM, or a short note saying why there are none."""
    query = " ".join(str(query or "").split())[:300]
    if not api_key:
        return "Поиск в интернете не подключён."
    if not query:
        return "Пустой запрос."
    if _used.get(today, 0) >= daily_limit:
        return "Лимит поисков на сегодня исчерпан, ответь без поиска и не называй конкретных мест."
    if today not in _used:
        _used.clear()  # only today's count matters
    _used[today] = _used.get(today, 0) + 1
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            resp = await client.post(SEARCH_URL, headers={"Authorization": f"Bearer {api_key}"},
                                     json={"query": query, "max_results": MAX_RESULTS})
            resp.raise_for_status()
            text = format_results(resp.json())
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Search failed: %s %s", type(e).__name__, e)
        return "Поиск сейчас не работает, ответь без него и не называй конкретных мест."
    return text or "Ничего не нашлось."
