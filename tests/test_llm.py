import unittest
from datetime import datetime

from girls_agent import llm


class BuildMessagesTest(unittest.TestCase):
    def test_question_only(self):
        messages = llm.build_messages("как дела?", datetime(2026, 10, 27, 14, 30), author="Аня")
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn("2026-10-27 14:30, вторник", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "Аня: как дела?")

    def test_reply_to_gigi_becomes_assistant_turn(self):
        messages = llm.build_messages("а почему?", datetime(2026, 10, 27, 14, 30), replied_text="Спать пора 🌙",
                                      replied_is_gigi=True)
        self.assertEqual([m["role"] for m in messages], ["system", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "Спать пора 🌙")

    def test_reply_to_someone_else_is_quoted(self):
        messages = llm.build_messages("что скажешь?", datetime(2026, 10, 27, 14, 30), replied_text="Идём в кино?")
        self.assertEqual(len(messages), 2)
        self.assertIn("«Идём в кино?»", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
