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


DEFAULT_NEWS_FEEDS = ("https://feeds.bbci.co.uk/news/science_and_environment/rss.xml,"
                      "https://feeds.bbci.co.uk/news/technology/rss.xml")

# Lite models first: on the free tier they have higher daily limits; stronger models are the fallback
DEFAULT_LLM_MODELS = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash,gemini-3.6-flash,gemini-3.8-flash"


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
    morning_time: Optional[tuple]    # None = no morning message
    kids_picks_time: Optional[tuple]  # Fridays; None = no kids' weekend picks
    kids_picks_city: str              # searched for events, in Latin letters
    weather_city: str                # shown in messages
    weather_lat: float
    weather_lon: float
    news_feeds: tuple                # RSS feeds for the morning's science / tech story
    llm_api_key: Optional[str]       # None = the chat assistant is off
    llm_base_url: str                # any OpenAI-compatible API
    llm_models: tuple                # tried in order until one answers
    llm_daily_limit: int             # answers per chat per day
    search_api_key: Optional[str]    # Tavily; None = no web search, Gigi doesn't name places
    search_daily_limit: int          # searches per day for the whole bot
    allowed_chats: tuple             # group chat ids the bot works in; empty = any chat
    group_file: str                  # text file describing the group and its members, for the LLM
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
        goodnight_time=_optional_hhmm(os.environ.get("GOODNIGHT_TIME", "off")),
        morning_time=_optional_hhmm(os.environ.get("MORNING_TIME", "off")),
        kids_picks_time=_optional_hhmm(os.environ.get("KIDS_PICKS_TIME", "off")),
        kids_picks_city=os.environ.get("KIDS_PICKS_CITY", "Amsterdam"),
        weather_city=os.environ.get("WEATHER_CITY", "Амстердам"),
        weather_lat=float(os.environ.get("WEATHER_LAT", "52.37")),
        weather_lon=float(os.environ.get("WEATHER_LON", "4.90")),
        news_feeds=tuple(u.strip() for u in os.environ.get("NEWS_FEEDS", DEFAULT_NEWS_FEEDS).split(",") if u.strip()),
        llm_api_key=os.environ.get("LLM_API_KEY") or None,
        llm_base_url=os.environ.get("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        llm_models=tuple(m.strip() for m in os.environ.get(
            "LLM_MODELS", DEFAULT_LLM_MODELS).split(",") if m.strip()),
        llm_daily_limit=int(os.environ.get("LLM_DAILY_LIMIT", "50")),
        search_api_key=os.environ.get("TAVILY_API_KEY") or None,
        search_daily_limit=int(os.environ.get("SEARCH_DAILY_LIMIT", "30")),
        allowed_chats=_int_list(os.environ.get("ALLOWED_CHATS", "")),
        group_file=os.environ.get("GROUP_FILE", "group.txt"),
        date_poll_days=int(os.environ.get("DATE_POLL_DAYS", "3")),
        activity_poll_hours=int(os.environ.get("ACTIVITY_POLL_HOURS", "48")),
    )
