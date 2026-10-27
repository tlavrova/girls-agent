import asyncio
import json
import unittest
from datetime import datetime

import httpx

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


class CompleteTest(unittest.TestCase):
    def setUp(self):
        llm._exhausted.clear()

    def run_complete(self, handler, models):
        calls = []

        def record(request):
            model = json.loads(request.content)["model"]
            calls.append(model)
            return handler(model)

        answer = asyncio.run(llm.complete("https://example.test/v1", "key", models, [],
                                          transport=httpx.MockTransport(record)))
        return answer, calls

    @staticmethod
    def ok(text):
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    def test_falls_back_and_skips_exhausted_model(self):
        handler = lambda m: httpx.Response(429, json={}) if m == "a" else self.ok("привет")
        self.assertEqual(self.run_complete(handler, ["a", "b"]), ("привет", ["a", "b"]))
        # "a" hit its quota, so the next answer goes straight to "b"
        self.assertEqual(self.run_complete(handler, ["a", "b"]), ("привет", ["b"]))

    def test_overloaded_model_is_retried_next_time(self):
        handler = lambda m: httpx.Response(503, json={}) if m == "a" else self.ok("да")
        self.run_complete(handler, ["a", "b"])
        self.assertEqual(self.run_complete(handler, ["a", "b"])[1], ["a", "b"])

    def test_empty_when_all_fail(self):
        self.assertEqual(self.run_complete(lambda m: httpx.Response(500), ["a"])[0], "")


class ToolCallTest(unittest.TestCase):
    def test_parses_first_valid_call(self):
        message = {"tool_calls": [
            {"id": "1", "function": {"name": "create_poll", "arguments": "not json"}},
            {"id": "2", "function": {"name": "get_weather", "arguments": '{"date": "2026-10-31"}'}},
        ]}
        self.assertEqual(llm.tool_call(message), ("get_weather", {"date": "2026-10-31"}, "2"))

    def test_none_without_calls(self):
        self.assertIsNone(llm.tool_call({"content": "привет"}))

    def test_chat_returns_tool_calls(self):
        reply = {"content": None, "tool_calls": [{"id": "x", "function": {"name": "get_weather", "arguments": "{}"}}]}
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": reply}]}))
        message = asyncio.run(llm.chat("https://example.test", "k", ["m"], [], tools=llm.TOOLS, transport=transport))
        self.assertEqual(llm.tool_call(message)[0], "get_weather")


if __name__ == "__main__":
    unittest.main()
