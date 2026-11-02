import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from girls_agent import bot, llm
from girls_agent.db import Database

CHAT = -100


def msg(message_id, text, reply=None):
    return SimpleNamespace(chat_id=CHAT, message_id=message_id, text=text, caption=None, reply_to_message=reply)


class ThreadTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def chain(self, n):
        """n alternating turns, 1 = Таня asks, 2 = Gigi answers, ...; returns the last as a Telegram message."""
        for i in range(1, n + 1):
            self.db.add_turn(CHAT, i, f"m{i}", reply_to=i - 1 or None, author="" if i % 2 == 0 else "Таня",
                             is_gigi=i % 2 == 0)
        return msg(n, f"m{n}")

    def test_follows_the_chain_oldest_first(self):
        reply = self.chain(4)  # the question replies to m4
        self.assertEqual(bot.thread_before(self.db, CHAT, reply),
                         [(False, "Таня", "m1"), (True, "", "m2"), (False, "Таня", "m3")])

    def test_at_most_depth_messages(self):
        thread = bot.thread_before(self.db, CHAT, self.chain(10))
        self.assertEqual(len(thread), bot.THREAD_DEPTH - 1)  # plus the replied-to message itself
        self.assertEqual(thread[-1][2], "m9")

    def test_stops_at_an_unknown_message_and_loops(self):
        self.assertEqual(bot.thread_before(self.db, CHAT, msg(99, "старое")), [])
        self.assertEqual(bot.thread_before(self.db, CHAT, None), [])
        self.db.add_turn(CHAT, 1, "a", reply_to=2)
        self.db.add_turn(CHAT, 2, "b", reply_to=1)
        self.assertLessEqual(len(bot.thread_before(self.db, CHAT, msg(2, "b"))), bot.THREAD_DEPTH - 1)

    def test_gigi_replies_are_saved(self):
        sent = SimpleNamespace(chat_id=CHAT, message_id=11, text="Записала 📌 ужин")
        question = msg(10, "@gigi запомни ужин")
        question.reply_text = AsyncMock(return_value=sent)
        asyncio.run(bot.Recorded(question, self.db).reply_text("Записала"))
        turn = self.db.get_turn(CHAT, 11)
        self.assertEqual((turn["text"], turn["reply_to"], turn["is_gigi"]), ("Записала 📌 ужин", 10, 1))

    def test_old_turns_are_forgotten(self):
        self.db.add_turn(CHAT, 1, "a")
        self.assertEqual(self.db.delete_turns_before(datetime.now(timezone.utc) - timedelta(days=3)), 0)
        self.assertEqual(self.db.delete_turns_before(datetime.now(timezone.utc) + timedelta(seconds=1)), 1)

    def test_thread_goes_before_the_reply(self):
        messages = llm.build_messages("а вторая?", datetime(2026, 11, 2, 12), author="Таня",
                                      thread=[(False, "Таня", "найди ресторан"), (True, "", "1. X 2. Y")],
                                      replied_text="Сохранила X", replied_is_gigi=True)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn("Таня: найди ресторан\nGigi: 1. X 2. Y\nGigi: Сохранила X\n(конец переписки)\nа вторая?",
                      messages[1]["content"])

if __name__ == "__main__":
    unittest.main()
