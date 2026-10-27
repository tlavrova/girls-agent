from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _int_list(value: str) -> tuple:
    return tuple(int(x) for x in value.split(",") if x.strip())


def _hhmm(value: str) -> tuple:
    hours, minutes = value.split(":")
    return int(hours), int(minutes)


def _optional_hhmm(value: str) -> Optional[tuple]:
    """HH:MM, or None when the message is switched off (empty or "off")."""
    value = value.strip().lower()
    return None if value in ("", "off") else _hhmm(value)


@dataclass(frozen=True)
class Config:
    token: str
    tz: ZoneInfo
    db_path: str
    meeting_weekdays: tuple   # 0 = Monday … 6 = Sunday
    poll_day: int             # day of the month the poll for next month starts (1-28)
    poll_time: tuple          # (hours, minutes)
    reminder_time: tuple
    goodnight_time: Optional[tuple]  # None = no good-night message
    llm_api_key: Optional[str]       # None = the chat assistant is off
    llm_base_url: str                # any OpenAI-compatible API
    llm_model: str
    llm_daily_limit: int             # answers per chat per day
    date_poll_days: int
    activity_poll_hours: int


def load_config() -> Config:
    load_dotenv()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN is not set: copy .env.example to .env and paste the token from @BotFather")
    poll_day = int(os.environ.get("POLL_DAY", "20"))
    if not 1 <= poll_day <= 28:
        raise SystemExit("POLL_DAY must be between 1 and 28 so it exists in every month")
    return Config(
        token=token,
        tz=ZoneInfo(os.environ.get("TIMEZONE", "Europe/Amsterdam")),
        db_path=os.environ.get("DB_PATH", "girls_agent.sqlite3"),
        meeting_weekdays=_int_list(os.environ.get("MEETING_WEEKDAYS", "5,6")),
        poll_day=poll_day,
        poll_time=_hhmm(os.environ.get("POLL_TIME", "11:00")),
        reminder_time=_hhmm(os.environ.get("REMINDER_TIME", "18:00")),
        goodnight_time=_optional_hhmm(os.environ.get("GOODNIGHT_TIME", "23:00")),
        llm_api_key=os.environ.get("LLM_API_KEY") or None,
        llm_base_url=os.environ.get("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        llm_model=os.environ.get("LLM_MODEL", "gemini-3.8-flash"),
        llm_daily_limit=int(os.environ.get("LLM_DAILY_LIMIT", "50")),
        date_poll_days=int(os.environ.get("DATE_POLL_DAYS", "3")),
        activity_poll_hours=int(os.environ.get("ACTIVITY_POLL_HOURS", "48")),
    )
