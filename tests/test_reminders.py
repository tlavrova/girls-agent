import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from girls_agent import bot, llm
from girls_agent.db import Database

TZ = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)  # Monday


class SaveReminderTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.message = SimpleNamespace(chat_id=-100, from_user=SimpleNamespace(id=1, full_name="Таня", username=None),
                                       reply_text=AsyncMock())

    def save(self, args):
        asyncio.run(bot.save_requested_reminder(self.message, self.db, args, NOW))
        return self.message.reply_text.call_args.args[0]

    def test_day_before_for_everyone_at_default_time(self):
        reply = self.save({"date": "2026-11-06", "text": "завтра в 19:00 ужин в Moeders"})
        r = self.db.reminders(-100)[0]
        self.assertIsNone(r["user_id"])
        self.assertEqual(datetime.fromisoformat(r["at"]), datetime(2026, 11, 6, 10, 0, tzinfo=TZ))
        self.assertIn("Напомню всем", reply)
        self.assertIn("10:00", reply)
        self.assertIn(f"/unremind {r['id']}", reply)

    def test_only_me(self):
        reply = self.save({"date": "2026-11-06", "time": "09:30", "text": "взять купальник", "everyone": False})
        r = self.db.reminders(-100)[0]
        self.assertEqual((r["user_id"], r["added_by"]), (1, "Таня"))
        self.assertIn("Напомню тебе", reply)
        self.assertIn("09:30", reply)

    def test_past_and_unclear(self):
        self.assertIn("уже прошло", self.save({"date": "2026-11-02", "time": "11:00", "text": "x"}))
        self.assertIn("Не поняла", self.save({"date": "завтра", "text": "x"}))
        self.assertIn("Не поняла", self.save({"date": "2026-11-06", "text": " "}))
        self.assertEqual(self.db.reminders(-100), [])

    def test_delete(self):
        self.save({"date": "2026-11-06", "text": "ужин"})
        r = self.db.reminders(-100)[0]
        asyncio.run(bot.delete_requested_reminder(self.message, self.db, f"#{r['id']}", TZ))
        self.assertIn("Отменила", self.message.reply_text.call_args.args[0])
        self.assertEqual(self.db.reminders(-100), [])
        self.assertIsNone(self.db.delete_reminder(-200, r["id"]))  # never another chat's


class SendReminderTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.upsert_member(-100, 1, "Таня")
        self.db.upsert_member(-100, 2, "Аня")
        self.tg = SimpleNamespace(send_message=AsyncMock())

    def test_due_ones_are_sent_once_with_mentions(self):
        now = datetime.now(timezone.utc)
        self.db.add_reminder(-100, now - timedelta(minutes=1), "ужин в <Moeders>")
        self.db.add_reminder(-100, now - timedelta(minutes=1), "купальник", user_id=1, added_by="Таня")
        self.db.add_reminder(-100, now + timedelta(hours=1), "позже")
        due = self.db.due_reminders(now)
        self.assertEqual([r["text"] for r in due], ["ужин в <Moeders>", "купальник"])
        for r in due:
            asyncio.run(bot.send_reminder(self.tg, self.db, r))
        asyncio.run(bot.send_reminder(self.tg, self.db, due[0]))  # a second run doesn't send it again

        self.assertEqual(self.tg.send_message.call_count, 2)
        everyone, mine = [c.args[1] for c in self.tg.send_message.call_args_list]
        self.assertIn("ужин в &lt;Moeders&gt;", everyone)
        self.assertIn("tg://user?id=1", everyone)
        self.assertIn("tg://user?id=2", everyone)
        self.assertIn("tg://user?id=1", mine)
        self.assertNotIn("tg://user?id=2", mine)
        self.assertEqual([r["text"] for r in self.db.reminders(-100)], ["позже"])


class PromptTest(unittest.TestCase):
    def test_reminders_listed_for_the_model(self):
        db = Database(":memory:")
        db.add_reminder(-100, datetime(2026, 11, 6, 10, 0, tzinfo=TZ), "ужин", user_id=None)
        listed = bot.reminders_for_llm(db, -100, TZ)
        self.assertRegex(listed, r"^\[#1\] .*6 ноября, 10:00 — всем: ужин$")
        system = llm.build_messages("?", NOW.replace(tzinfo=None), reminders=listed)[0]["content"]
        self.assertIn("Напоминания:\n[#1]", system)
        self.assertIn("set_reminder", [t["function"]["name"] for t in llm.tools(False)])


if __name__ == "__main__":
    unittest.main()
