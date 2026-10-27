import asyncio
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from girls_agent import bot, weather


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
