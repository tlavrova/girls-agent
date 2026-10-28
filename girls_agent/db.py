"""SQLite storage: chats, members, polls, votes, ideas, meetings (the shared agenda), plans, recommendations and facts."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional, Sequence

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    title   TEXT
);
CREATE TABLE IF NOT EXISTS members (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    name    TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS polls (
    poll_id    TEXT PRIMARY KEY,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    kind       TEXT NOT NULL,          -- 'date' | 'activity'
    options    TEXT NOT NULL,          -- JSON: option texts
    payload    TEXT NOT NULL,          -- JSON: dates / ideas / meeting_id
    status     TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    closes_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
    poll_id    TEXT NOT NULL,
    user_id    INTEGER NOT NULL,
    option_ids TEXT NOT NULL,          -- JSON
    PRIMARY KEY (poll_id, user_id)
);
CREATE TABLE IF NOT EXISTS ideas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '',
    added_by   TEXT,
    used       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meetings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    date       TEXT NOT NULL,          -- YYYY-MM-DD
    activity   TEXT,
    details    TEXT,
    status     TEXT NOT NULL DEFAULT 'planning',  -- 'planning' | 'planned' | 'cancelled'
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (  -- things and services friends recommend; not meetup ideas
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id        INTEGER NOT NULL,
    category       TEXT NOT NULL,
    title          TEXT NOT NULL,
    details        TEXT NOT NULL DEFAULT '',
    recommended_by TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (           -- "запомни, я не люблю суши": what someone told Gigi about herself
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    fact       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (           -- "запомни, в субботу в 19:00 идём в кафе X"
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    date       TEXT NOT NULL,          -- YYYY-MM-DD
    time       TEXT,                   -- HH:MM
    what       TEXT NOT NULL,
    place      TEXT NOT NULL DEFAULT '',
    who        TEXT NOT NULL DEFAULT '',
    added_by   TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    # --- chats and members ---

    def upsert_chat(self, chat_id: int, title: Optional[str]) -> None:
        self._exec(
            "INSERT INTO chats (chat_id, title) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title",
            (chat_id, title),
        )

    def chat_ids(self) -> List[int]:
        return [r["chat_id"] for r in self.conn.execute("SELECT chat_id FROM chats")]

    def chats(self) -> List[sqlite3.Row]:
        return list(self.conn.execute("SELECT chat_id, title FROM chats"))

    def is_member(self, user_id: int, chat_ids: Sequence[int]) -> bool:
        """True if the user is known in any of these chats."""
        if not chat_ids:
            return False
        marks = ",".join("?" * len(chat_ids))
        return self.conn.execute(
            f"SELECT 1 FROM members WHERE user_id = ? AND chat_id IN ({marks}) LIMIT 1",
            (user_id, *chat_ids)).fetchone() is not None

    def upsert_member(self, chat_id: int, user_id: int, name: str) -> None:
        self._exec(
            "INSERT INTO members (chat_id, user_id, name) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET name = excluded.name",
            (chat_id, user_id, name),
        )

    def remove_member(self, chat_id: int, user_id: int) -> None:
        self._exec("DELETE FROM members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))

    def members(self, chat_id: int) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT user_id, name FROM members WHERE chat_id = ? ORDER BY name", (chat_id,)))

    # --- polls and votes ---

    def add_poll(self, poll_id: str, chat_id: int, message_id: int, kind: str,
                 options: list, payload: dict, closes_at: datetime) -> None:
        self._exec(
            "INSERT INTO polls (poll_id, chat_id, message_id, kind, options, payload, created_at, closes_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (poll_id, chat_id, message_id, kind, json.dumps(options, ensure_ascii=False),
             json.dumps(payload, ensure_ascii=False), _now(), closes_at.astimezone(timezone.utc).isoformat()),
        )

    def get_poll(self, poll_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM polls WHERE poll_id = ?", (poll_id,)).fetchone()

    def open_polls(self, chat_id: Optional[int] = None) -> List[sqlite3.Row]:
        if chat_id is None:
            return list(self.conn.execute("SELECT * FROM polls WHERE status = 'open'"))
        return list(self.conn.execute(
            "SELECT * FROM polls WHERE status = 'open' AND chat_id = ? ORDER BY created_at DESC", (chat_id,)))

    def due_polls(self, now: datetime) -> List[sqlite3.Row]:
        return [p for p in self.open_polls() if datetime.fromisoformat(p["closes_at"]) <= now]

    def close_poll(self, poll_id: str) -> bool:
        """Atomically close a poll; False if it was already closed (guards against double closing)."""
        cur = self._exec("UPDATE polls SET status = 'closed' WHERE poll_id = ? AND status = 'open'", (poll_id,))
        return cur.rowcount == 1

    def record_vote(self, poll_id: str, user_id: int, option_ids: list) -> None:
        if option_ids:
            self._exec(
                "INSERT INTO votes (poll_id, user_id, option_ids) VALUES (?, ?, ?) "
                "ON CONFLICT(poll_id, user_id) DO UPDATE SET option_ids = excluded.option_ids",
                (poll_id, user_id, json.dumps(list(option_ids))),
            )
        else:  # the member retracted her vote
            self._exec("DELETE FROM votes WHERE poll_id = ? AND user_id = ?", (poll_id, user_id))

    def votes(self, poll_id: str) -> List[list]:
        return [json.loads(r["option_ids"]) for r in
                self.conn.execute("SELECT option_ids FROM votes WHERE poll_id = ?", (poll_id,))]

    def voter_ids(self, poll_id: str) -> set:
        return {r["user_id"] for r in self.conn.execute("SELECT user_id FROM votes WHERE poll_id = ?", (poll_id,))}

    # --- ideas ---

    def add_idea(self, chat_id: int, kind: str, title: str, details: str = "", added_by: str = "") -> int:
        cur = self._exec(
            "INSERT INTO ideas (chat_id, kind, title, details, added_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, kind, title, details, added_by, _now()),
        )
        return cur.lastrowid

    def unused_ideas(self, chat_id: int) -> List[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT id, kind, title, details, added_by FROM ideas WHERE chat_id = ? AND used = 0 ORDER BY id DESC",
            (chat_id,))]

    def mark_idea_used(self, idea_id: int) -> None:
        self._exec("UPDATE ideas SET used = 1 WHERE id = ?", (idea_id,))

    def delete_idea(self, chat_id: int, idea_id: int) -> Optional[sqlite3.Row]:
        """The deleted idea, or None if this chat has no such idea."""
        row = self.conn.execute("SELECT * FROM ideas WHERE id = ? AND chat_id = ?", (idea_id, chat_id)).fetchone()
        if row:
            self._exec("DELETE FROM ideas WHERE id = ?", (idea_id,))
        return row

    # --- recommendations ---

    def add_recommendation(self, chat_id: int, category: str, title: str, details: str = "",
                           recommended_by: str = "") -> int:
        cur = self._exec(
            "INSERT INTO recommendations (chat_id, category, title, details, recommended_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, category, title, details, recommended_by, _now()),
        )
        return cur.lastrowid

    def recommendations(self, chat_id: int, category: Optional[str] = None) -> List[sqlite3.Row]:
        if category:
            return list(self.conn.execute(
                "SELECT * FROM recommendations WHERE chat_id = ? AND category = ? ORDER BY id DESC",
                (chat_id, category)))
        return list(self.conn.execute(
            "SELECT * FROM recommendations WHERE chat_id = ? ORDER BY category, id DESC", (chat_id,)))

    def delete_recommendation(self, chat_id: int, rec_id: int) -> Optional[sqlite3.Row]:
        row = self.conn.execute(
            "SELECT * FROM recommendations WHERE id = ? AND chat_id = ?", (rec_id, chat_id)).fetchone()
        if row:
            self._exec("DELETE FROM recommendations WHERE id = ?", (rec_id,))
        return row

    # --- meetings (agenda) ---

    def add_meeting(self, chat_id: int, meeting_date: str) -> int:
        cur = self._exec(
            "INSERT INTO meetings (chat_id, date, created_at) VALUES (?, ?, ?)",
            (chat_id, meeting_date, _now()),
        )
        return cur.lastrowid

    def set_meeting_plan(self, meeting_id: int, activity: str, details: str) -> None:
        self._exec(
            "UPDATE meetings SET activity = ?, details = ?, status = 'planned' WHERE id = ?",
            (activity, details, meeting_id),
        )

    def cancel_meeting(self, meeting_id: int) -> None:
        self._exec("UPDATE meetings SET status = 'cancelled' WHERE id = ?", (meeting_id,))

    def get_meeting(self, meeting_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()

    def has_meeting_in_month(self, chat_id: int, year: int, month: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM meetings WHERE chat_id = ? AND status != 'cancelled' AND date LIKE ?",
            (chat_id, f"{year:04d}-{month:02d}-%"),
        ).fetchone()
        return row is not None

    def upcoming_meetings(self, chat_id: int, from_date: str) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM meetings WHERE chat_id = ? AND status != 'cancelled' AND date >= ? ORDER BY date",
            (chat_id, from_date),
        ))

    # --- plans (saved from chat) ---

    def add_plan(self, chat_id: int, plan_date: str, what: str, *, time: Optional[str] = None,
                 place: str = "", who: str = "", added_by: str = "") -> int:
        cur = self._exec(
            "INSERT INTO plans (chat_id, date, time, what, place, who, added_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, plan_date, time, what, place, who, added_by, _now()),
        )
        return cur.lastrowid

    def get_plan(self, chat_id: int, plan_id: int) -> Optional[sqlite3.Row]:
        """The plan, only if it belongs to this chat."""
        return self.conn.execute("SELECT * FROM plans WHERE id = ? AND chat_id = ?", (plan_id, chat_id)).fetchone()

    def update_plan(self, chat_id: int, plan_id: int, plan_date: str, what: str, *, time: Optional[str],
                    place: str, who: str) -> None:
        self._exec(
            "UPDATE plans SET date = ?, time = ?, what = ?, place = ?, who = ? WHERE id = ? AND chat_id = ?",
            (plan_date, time, what, place, who, plan_id, chat_id),
        )

    def delete_plan(self, chat_id: int, plan_id: int) -> bool:
        return self._exec("DELETE FROM plans WHERE id = ? AND chat_id = ?", (plan_id, chat_id)).rowcount > 0

    def delete_plans_before(self, before_date: str) -> int:
        """Forget plans that are long past: nothing about where people went is kept longer than needed."""
        return self._exec("DELETE FROM plans WHERE date < ?", (before_date,)).rowcount

    def upcoming_plans(self, chat_id: int, from_date: str) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM plans WHERE chat_id = ? AND date >= ? ORDER BY date, time",
            (chat_id, from_date),
        ))

    # --- facts about members ---

    def add_fact(self, chat_id: int, user_id: int, fact: str) -> int:
        cur = self._exec("INSERT INTO facts (chat_id, user_id, fact, created_at) VALUES (?, ?, ?, ?)",
                         (chat_id, user_id, fact, _now()))
        return cur.lastrowid

    def facts(self, chat_id: int, user_id: int) -> List[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT id, fact FROM facts WHERE chat_id = ? AND user_id = ? ORDER BY id", (chat_id, user_id)))

    def delete_fact(self, chat_id: int, user_id: int, fact_id: int) -> Optional[sqlite3.Row]:
        """The deleted fact, or None: people can only delete their own facts."""
        row = self.conn.execute("SELECT * FROM facts WHERE id = ? AND chat_id = ? AND user_id = ?",
                                (fact_id, chat_id, user_id)).fetchone()
        if row:
            self._exec("DELETE FROM facts WHERE id = ?", (fact_id,))
        return row
