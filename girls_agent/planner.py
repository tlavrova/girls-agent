"""Pure planning logic: candidate dates, vote counting, idea selection.

Nothing here knows about Telegram, which keeps the module easy to test.
User-facing strings are in Russian because the bot serves a Russian-speaking group.
"""
from __future__ import annotations

import calendar
import random
from datetime import date
from typing import Iterable, List, Optional, Sequence

WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
MONTHS_PREPOSITIONAL = [
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
]

# Idea kinds: restaurant, party, event, generic idea.
KIND_EMOJI = {
    "ресторан": "🍽",
    "вечеринка": "🎉",
    "мероприятие": "🎟",
    "идея": "✨",
}

# Telegram allows up to 10 poll options; one is reserved for "none of these work".
MAX_DATE_OPTIONS = 9
POLL_OPTION_MAX_LEN = 100


def next_month(today: date) -> tuple:
    if today.month == 12:
        return today.year + 1, 1
    return today.year, today.month + 1


def candidate_dates(today: date, weekdays: Iterable[int], limit: int = MAX_DATE_OPTIONS) -> List[date]:
    """All days of next month that fall on the given weekdays (0 = Monday)."""
    wanted = set(weekdays)
    year, month = next_month(today)
    days_in_month = calendar.monthrange(year, month)[1]
    dates = [date(year, month, d) for d in range(1, days_in_month + 1)]
    return [d for d in dates if d.weekday() in wanted][:limit]


def format_date(d: date) -> str:
    return f"{WEEKDAYS_SHORT[d.weekday()]}, {d.day} {MONTHS_GENITIVE[d.month - 1]}"


def month_prepositional(d: date) -> str:
    return MONTHS_PREPOSITIONAL[d.month - 1]


def pick_winner(counts: Sequence[int]) -> Optional[int]:
    """Index of the option with the most votes; ties go to the earliest. None if nobody voted."""
    if not counts or max(counts) == 0:
        return None
    best = max(counts)
    return counts.index(best)


def tally(votes: Iterable[Sequence[int]], n_options: int) -> List[int]:
    counts = [0] * n_options
    for option_ids in votes:
        for i in option_ids:
            if 0 <= i < n_options:
                counts[i] += 1
    return counts


def pick_ideas(own: Sequence[dict], seeds: Sequence[dict], n: int = 3,
               rng: Optional[random.Random] = None) -> List[dict]:
    """Pick n ideas: the group's own ideas first, then top up from the seed set.

    Tries to make the options different kinds (restaurant / party / event).
    """
    rng = rng or random.Random()
    picked: List[dict] = []
    seen_titles = set()

    def take(pool: Sequence[dict], diverse: bool) -> None:
        pool = list(pool)
        rng.shuffle(pool)
        for idea in pool:
            if len(picked) >= n:
                return
            if idea["title"] in seen_titles:
                continue
            if diverse and idea["kind"] in {p["kind"] for p in picked}:
                continue
            picked.append(idea)
            seen_titles.add(idea["title"])

    for pool in (own, seeds):
        take(pool, diverse=True)
    for pool in (own, seeds):
        take(pool, diverse=False)
    return picked


def idea_option(idea: dict) -> str:
    emoji = KIND_EMOJI.get(idea.get("kind", ""), "✨")
    return truncate(f"{emoji} {idea['title']}", POLL_OPTION_MAX_LEN)


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_idea(text: str) -> tuple:
    """'ресторан: Сыроварня, Москва' -> ('ресторан', 'Сыроварня, Москва')."""
    text = text.strip()
    head, sep, tail = text.partition(":")
    kind = head.strip().lower()
    if sep and kind in KIND_EMOJI and tail.strip():
        return kind, tail.strip()
    return "идея", text
