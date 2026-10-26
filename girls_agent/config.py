from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _int_list(value: str) -> tuple:
    return tuple(int(x) for x in value.split(",") if x.strip())


def _hhmm(value: str) -> tuple:
    hours, minutes = value.split(":")
    return int(hours), int(minutes)


@dataclass(frozen=True)
class Config:
    token: str
    tz: ZoneInfo
    db_path: str
    meeting_weekdays: tuple   # 0 = Monday … 6 = Sunday
    poll_weekday: int         # the poll starts on the LAST such weekday of the month (5 = Saturday)
    poll_time: tuple          # (hours, minutes)
    reminder_time: tuple
    date_poll_days: int
    activity_poll_hours: int


def load_config() -> Config:
    load_dotenv()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN is not set: copy .env.example to .env and paste the token from @BotFather")
    return Config(
        token=token,
        tz=ZoneInfo(os.environ.get("TIMEZONE", "Europe/Amsterdam")),
        db_path=os.environ.get("DB_PATH", "girls_agent.sqlite3"),
        meeting_weekdays=_int_list(os.environ.get("MEETING_WEEKDAYS", "5,6")),
        poll_weekday=int(os.environ.get("POLL_WEEKDAY", "5")),
        poll_time=_hhmm(os.environ.get("POLL_TIME", "11:00")),
        reminder_time=_hhmm(os.environ.get("REMINDER_TIME", "18:00")),
        date_poll_days=int(os.environ.get("DATE_POLL_DAYS", "3")),
        activity_poll_hours=int(os.environ.get("ACTIVITY_POLL_HOURS", "48")),
    )
