import asyncio
import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from girls_agent import bot, llm, search

TODAY = date(2026, 10, 29)
RESULTS = {"results": [
    {"title": "Trattoria X", "url": "https://x.nl", "content": "Oudegracht 1,   Utrecht.\nOpen daily."},
    {"title": "Pizza Y", "url": "https://y.nl", "content": "y" * 1000},
]}


class LookupTest(unittest.TestCase):
    def setUp(self):
        search._used.clear()

    def run_lookup(self, handler, key="tvly-test", limit=30, query="italiaans restaurant Utrecht"):
        return asyncio.run(search.lookup(key, query, TODAY, limit, transport=httpx.MockTransport(handler)))

    def test_formats_results(self):
        sent = {}

        def handler(request):
            sent["auth"] = request.headers["Authorization"]
            sent["body"] = json.loads(request.content)
            return httpx.Response(200, json=RESULTS)

        text = self.run_lookup(handler)
        self.assertEqual(sent["auth"], "Bearer tvly-test")
        self.assertEqual(sent["body"]["query"], "italiaans restaurant Utrecht")
        self.assertIn("1. Trattoria X — https://x.nl\nOudegracht 1, Utrecht. Open daily.", text)
        self.assertIn("2. Pizza Y — https://y.nl\n" + "y" * search.SNIPPET + "", text)
        self.assertNotIn("y" * (search.SNIPPET + 1), text)

    def test_daily_limit(self):
        ok = lambda r: httpx.Response(200, json=RESULTS)
        self.run_lookup(ok, limit=1)
        self.assertIn("Лимит", self.run_lookup(ok, limit=1))

    def test_failure_and_no_key(self):
        self.assertIn("не работает", self.run_lookup(lambda r: httpx.Response(500)))
        self.assertIn("не подключён", self.run_lookup(lambda r: httpx.Response(200, json=RESULTS), key=None))
        self.assertIn("Ничего", self.run_lookup(lambda r: httpx.Response(200, json={"results": []})))


def call(name, args, call_id="c1"):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


class AnswerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(llm_base_url="", llm_api_key="k", llm_models=("m",), search_api_key="tvly-test",
                                   search_daily_limit=30)
        self.message = SimpleNamespace(chat_id=-100, reply_text=AsyncMock())

    def answer(self, replies):
        chat = AsyncMock(side_effect=replies)
        with patch.object(llm, "chat", chat), \
                patch.object(search, "lookup", AsyncMock(return_value="1. Trattoria X — https://x.nl")) as lookup:
            asyncio.run(bot.answer_with_llm(self.message, None, self.cfg, [{"role": "user", "content": "?"}],
                                            bot.datetime(2026, 10, 29, 12)))
        return chat, lookup

    def test_search_result_goes_back_to_the_model(self):
        chat, lookup = self.answer([call("web_search", {"query": "restaurant Utrecht"}),
                                    {"role": "assistant", "content": "Trattoria X — https://x.nl"}])
        self.assertEqual(lookup.call_args.args[1], "restaurant Utrecht")
        second = chat.call_args.args[3]
        self.assertEqual(second[-1], {"role": "tool", "tool_call_id": "c1", "content": "1. Trattoria X — https://x.nl"})
        self.assertEqual(self.message.reply_text.call_args.args[0], "Trattoria X — https://x.nl")
        self.assertTrue(self.message.reply_text.call_args.kwargs["link_preview_options"].is_disabled)

    def test_searches_are_capped_per_answer(self):
        replies = [call("web_search", {"query": f"q{i}"}, f"c{i}") for i in range(3)] + [
            {"role": "assistant", "content": "готово"}]
        chat, lookup = self.answer(replies)
        self.assertEqual(lookup.call_count, bot.MAX_SEARCHES)
        self.assertIn("Хватит поисков", chat.call_args.args[3][-1]["content"])

    def test_search_tool_offered_only_with_key(self):
        names = lambda tools: [t["function"]["name"] for t in tools]
        self.assertIn("web_search", names(llm.tools(True)))
        self.assertNotIn("web_search", names(llm.tools(False)))
        self.assertIn("web_search", llm.build_messages("?", bot.datetime(2026, 10, 29), search=True)[0]["content"])
        self.assertNotIn("web_search", llm.build_messages("?", bot.datetime(2026, 10, 29))[0]["content"])


if __name__ == "__main__":
    unittest.main()
