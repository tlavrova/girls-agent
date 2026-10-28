import asyncio
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from girls_agent import bot, weather
from girls_agent.db import Database


class RequestedPollTest(unittest.TestCase):
    def fake_message(self):
        chat = SimpleNamespace(send_poll=AsyncMock())
        return SimpleNamespace(chat=chat, message_id=7, reply_text=AsyncMock())

    def test_creates_poll_with_clean_options(self):
        message = self.fake_message()
        asyncio.run(bot.create_requested_poll(message, {
            "question": "Куда идём?", "options": ["Кино", " кино ", "Кино", "Бар", ""], "multiple": True}))
        kwargs = message.chat.send_poll.call_args.kwargs
        self.assertEqual(kwargs["question"], "Куда идём?")
        self.assertEqual(kwargs["options"], ["Кино", "кино", "Бар"])
        self.assertTrue(kwargs["allows_multiple_answers"])
        self.assertEqual(kwargs["reply_to_message_id"], 7)

    def test_needs_two_options(self):
        message = self.fake_message()
        asyncio.run(bot.create_requested_poll(message, {"question": "?", "options": ["Кино"]}))
        message.chat.send_poll.assert_not_called()
        message.reply_text.assert_called_once()

    def test_parse_date(self):
        self.assertEqual(bot._parse_date("2026-10-31", date(2026, 1, 1)), date(2026, 10, 31))
        self.assertEqual(bot._parse_date("завтра", date(2026, 1, 1)), date(2026, 1, 1))
        self.assertEqual(bot._parse_date(None, date(2026, 1, 1)), date(2026, 1, 1))


class WeatherLookupTest(unittest.TestCase):
    def test_too_far_ahead(self):
        text = asyncio.run(weather.lookup(None, None, date(2026, 12, 31), date(2026, 10, 27)))
        self.assertIn("15", text)


if __name__ == "__main__":
    unittest.main()


class WhitelistTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.upsert_member(-100, 1, "Аня")

    def update(self, chat_id, chat_type, user_id=2):
        chat = SimpleNamespace(id=chat_id, type=chat_type, PRIVATE="private")
        return SimpleNamespace(effective_chat=chat, effective_user=SimpleNamespace(id=user_id))

    def allowed(self, update, chats=(-100,)):
        return bot.is_allowed(update, self.db, SimpleNamespace(allowed_chats=chats))

    def test_no_whitelist_allows_everyone(self):
        self.assertTrue(self.allowed(self.update(-999, "group"), chats=()))

    def test_groups(self):
        self.assertTrue(self.allowed(self.update(-100, "supergroup")))
        self.assertFalse(self.allowed(self.update(-999, "supergroup")))

    def test_private_only_for_members(self):
        self.assertTrue(self.allowed(self.update(1, "private", user_id=1)))
        self.assertFalse(self.allowed(self.update(2, "private", user_id=2)))

    def test_poll_answers_pass(self):
        self.assertTrue(self.allowed(SimpleNamespace(effective_chat=None, effective_user=None)))

    def test_scheduled_messages_skip_other_chats(self):
        self.db.upsert_chat(-100, "Наши")
        self.db.upsert_chat(-999, "Чужие")
        self.assertEqual(bot.group_chats(self.db, SimpleNamespace(allowed_chats=(-100,))), [-100])


class PlansTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.today = date(2026, 10, 28)

    def fake_message(self):
        return SimpleNamespace(chat_id=-100, from_user=SimpleNamespace(full_name="Таня", username=None, id=1),
                               reply_text=AsyncMock())

    def save(self, args):
        message = self.fake_message()
        asyncio.run(bot.save_requested_plan(message, self.db, args, self.today))
        return message.reply_text.call_args.args[0]

    def test_saves_and_confirms(self):
        reply = self.save({"date": "2026-10-31", "time": "19:00", "what": "ужин", "place": "кафе X", "who": "Таня, Аня"})
        self.assertIn("сб, 31 октября, 19:00 — ужин, кафе X (Таня, Аня)", reply)
        plan = self.db.upcoming_plans(-100, "2026-10-28")[0]
        self.assertEqual((plan["what"], plan["added_by"]), ("ужин", "Таня"))

    def test_bad_time_is_dropped(self):
        self.save({"date": "2026-10-31", "time": "вечером", "what": "кино"})
        self.assertIsNone(self.db.upcoming_plans(-100, "2026-10-28")[0]["time"])

    def test_rejects_past_and_unparsable_dates(self):
        self.save({"date": "2026-10-01", "what": "кино"})
        self.save({"date": "в субботу", "what": "кино"})
        self.assertEqual(self.db.upcoming_plans(-100, "2000-01-01"), [])

    def test_plans_for_llm_merges_meetups_and_plans_by_date(self):
        self.db.add_plan(-100, "2026-11-07", "йога")
        meeting = self.db.add_meeting(-100, "2026-11-01")
        self.db.set_meeting_plan(meeting, "боулинг", "")
        self.db.add_plan(-100, "2026-10-20", "прошлое")
        lines = bot.plans_for_llm(self.db, -100, self.today).splitlines()
        self.assertEqual(lines, ["вс, 1 ноября — общая встреча: боулинг", "сб, 7 ноября — йога"])
