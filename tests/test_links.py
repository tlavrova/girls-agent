import asyncio
import json
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from telegram import Chat, Message, MessageEntity, User

from girls_agent import bot, llm, search

TODAY = date(2026, 11, 2)
CHAT = Chat(-100, "group")


def message(text=None, caption=None, entities=(), caption_entities=()):
    return Message(1, datetime(2026, 11, 2), CHAT, text=text, caption=caption,
                   entities=list(entities), caption_entities=list(caption_entities))


class LinksTest(unittest.TestCase):
    def test_plain_and_hidden_links(self):
        text = "смотри x.nl/menu и вот тут"
        msg = message(text, entities=[MessageEntity("url", 7, 9),
                                      MessageEntity("text_link", 21, 3, url="https://event.nl/a")])
        self.assertEqual(bot.links(msg), ["https://x.nl/menu", "https://event.nl/a"])

    def test_links_and_mentions_in_a_caption(self):
        gigi = SimpleNamespace(id=42, username="gigi_bot")
        caption = "@gigi_bot сохрани https://x.nl"
        msg = message(caption=caption, caption_entities=[MessageEntity("mention", 0, 9), MessageEntity("url", 18, 12)])
        self.assertTrue(bot.addresses_gigi(msg, gigi))
        self.assertEqual(bot.links(msg), ["https://x.nl"])
        self.assertFalse(bot.addresses_gigi(message("привет"), gigi))


class ReadPagesTest(unittest.TestCase):
    def setUp(self):
        search._used.clear()

    def read(self, handler, urls=("https://x.nl",), key="tvly-test", limit=30):
        return asyncio.run(search.read_pages(key, list(urls), TODAY, limit, transport=httpx.MockTransport(handler)))

    def test_page_text_for_the_model(self):
        sent = {}

        def handler(request):
            sent["url"], sent["body"] = str(request.url), json.loads(request.content)
            return httpx.Response(200, json={
                "results": [{"url": "https://x.nl", "raw_content": "Trattoria X\n\nOudegracht 1, Utrecht  € 25"}],
                "failed_results": [{"url": "https://y.nl", "error": "blocked"}]})

        text = self.read(handler, urls=["https://x.nl", "https://y.nl", "https://x.nl", "https://z.nl"])
        self.assertEqual(sent["url"], search.EXTRACT_URL)
        self.assertEqual(sent["body"]["urls"], ["https://x.nl", "https://y.nl"])  # no duplicates, at most 2
        self.assertIn("Страница https://x.nl:\nTrattoria X Oudegracht 1, Utrecht € 25", text)
        self.assertIn("https://y.nl открыть не удалось", text)

    def test_menu_links_become_plain_text(self):
        raw = "![logo](https://x.nl/l.png) * [Menu](https://x.nl/menu) * [Contact](https://x.nl/c)\n## Moeders"
        self.assertEqual(search.page_text(raw), "Menu Contact ## Moeders")

    def test_nothing_to_read(self):
        fail = lambda r: self.fail("no request expected")
        self.assertEqual(self.read(fail, urls=[]), "")
        self.assertEqual(self.read(fail, key=None), "")

    def test_failure_and_limit_say_so(self):
        self.assertIn("не удалось", self.read(lambda r: httpx.Response(500)))
        search._used.clear()
        ok = lambda r: httpx.Response(200, json={"results": [{"url": "https://x.nl", "raw_content": "X"}]})
        self.read(ok, limit=1)
        self.assertIn("не удалось", self.read(ok, limit=1))
        # the limit is shared with web search
        self.assertIn("Лимит", asyncio.run(search.lookup("tvly-test", "q", TODAY, 1)))


class MessagesTest(unittest.TestCase):
    def test_pages_and_image_go_with_the_question(self):
        now = datetime(2026, 11, 2, 12)
        messages = llm.build_messages("сохрани", now, author="Таня", pages="Страница https://x.nl:\nTrattoria X",
                                      image="data:image/jpeg;base64,AAAA")
        content = messages[-1]["content"]
        self.assertIn("Таня: сохрани", content[0]["text"])
        self.assertIn("Trattoria X", content[0]["text"])
        self.assertEqual(content[1], {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}})
        self.assertEqual(llm.build_messages("привет", now)[-1]["content"], "привет")


class PhotoTest(unittest.TestCase):
    def test_photo_as_data_url(self):
        file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(b"img")))
        photo = SimpleNamespace(get_file=AsyncMock(return_value=file))
        msg = SimpleNamespace(photo=[SimpleNamespace(), photo])  # the largest size is last
        self.assertEqual(asyncio.run(bot.photo_url(msg)), "data:image/jpeg;base64,aW1n")
        self.assertIsNone(asyncio.run(bot.photo_url(SimpleNamespace(photo=()))))
        self.assertIsNone(asyncio.run(bot.photo_url(None)))


if __name__ == "__main__":
    unittest.main()
