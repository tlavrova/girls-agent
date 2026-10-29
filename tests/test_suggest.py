import asyncio
import json
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from girls_agent import bot, llm, search, suggest, weather
from girls_agent.db import Database

RESULTS = "1. Trattoria X — https://x.nl\nOudegracht 1\n\n2. Club Y — https://y.nl/events\nParty on 14 November"
CFG = SimpleNamespace(llm_api_key="k", search_api_key="tvly", llm_base_url="", llm_models=("m",),
                      search_daily_limit=30, meetup_city="Amsterdam", group_file="/nonexistent",
                      weather_city="Амстердам", tz=None)


def answer(*places):
    return "```json\n" + json.dumps(list(places), ensure_ascii=False) + "\n```"


class ParseTest(unittest.TestCase):
    def test_keeps_places_from_results(self):
        places = suggest.parse(answer(
            {"kind": "ресторан", "title": "Trattoria X", "details": "паста", "address": "Oudegracht 1", "url": "https://x.nl"},
            {"kind": "клуб", "title": "Club Y", "url": "https://y.nl/events"},
        ), RESULTS)
        self.assertEqual([p["title"] for p in places], ["Trattoria X", "Club Y"])
        self.assertEqual(places[0]["address"], "Oudegracht 1")
        self.assertEqual(places[1]["kind"], "мероприятие")  # unknown kinds become events

    def test_drops_made_up_links_and_junk(self):
        self.assertEqual(suggest.parse(answer({"title": "Herenzicht", "url": "https://herenzicht.nl"}), RESULTS), [])
        self.assertEqual(suggest.parse(answer({"title": "", "url": "https://x.nl"}), RESULTS), [])
        self.assertEqual(suggest.parse("не JSON", RESULTS), [])
        self.assertEqual(suggest.parse("[{oops", RESULTS), [])

    def test_queries_mention_the_date(self):
        self.assertIn("14 november 2026", suggest.queries("Amsterdam", date(2026, 11, 14))[1])


class RealPlacesTest(unittest.TestCase):
    def run_places(self, found, reply, n=3, cfg=CFG):
        with patch.object(search, "lookup", AsyncMock(return_value=found)) as lookup, \
                patch.object(weather, "lookup", AsyncMock(return_value="🌧 дождь")), \
                patch.object(llm, "complete", AsyncMock(return_value=reply)) as complete:
            places = asyncio.run(suggest.real_places(cfg, datetime(2026, 10, 29, 11), date(2026, 11, 14), n,
                                                     facts=["не ест мясо"], past=["Караоке"]))
        return places, lookup, complete

    def test_prompt_has_weather_facts_and_past(self):
        places, lookup, complete = self.run_places(RESULTS, answer({"title": "Trattoria X", "url": "https://x.nl"}))
        self.assertEqual([p["title"] for p in places], ["Trattoria X"])
        self.assertEqual(lookup.call_count, 2)
        prompt = complete.call_args.args[3][-1]["content"]
        for part in ("сб, 14 ноября", "🌧 дождь", "не ест мясо", "Караоке", "ровно 3"):
            self.assertIn(part, prompt)

    def test_no_search_no_places(self):
        places, _, complete = self.run_places("Поиск сейчас не работает", "[]")
        self.assertEqual(places, [])
        complete.assert_not_called()
        self.assertEqual(self.run_places(RESULTS, "[]", n=0)[0], [])
        no_key = SimpleNamespace(**{**vars(CFG), "search_api_key": None})
        self.assertEqual(self.run_places(RESULTS, "[]", cfg=no_key)[0], [])


class MeetupIdeasTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.cfg = SimpleNamespace(**{**vars(CFG), "tz": None})

    def ideas(self, real):
        with patch.object(suggest, "real_places", AsyncMock(return_value=real)) as real_places:
            ideas = asyncio.run(bot.meetup_ideas(self.db, self.cfg, -100, "2026-11-14"))
        return ideas, real_places

    def test_own_ideas_then_real_places(self):
        self.db.add_idea(-100, "ресторан", "De Kas")
        real = [{"kind": "мероприятие", "title": "Club Y", "details": "", "address": "", "url": "https://y.nl"},
                {"kind": "ресторан", "title": "Trattoria X", "details": "", "address": "", "url": "https://x.nl"}]
        ideas, real_places = self.ideas(real)
        self.assertEqual([i["title"] for i in ideas], ["De Kas", "Club Y", "Trattoria X"])
        self.assertEqual(real_places.call_args.args[3], 2)  # only the free slots

    def test_starter_ideas_fill_the_rest(self):
        ideas, _ = self.ideas([])
        self.assertEqual(len(ideas), 3)
        self.assertEqual(len({i["title"] for i in ideas}), 3)

    def test_details_carry_address_and_link(self):
        idea = {"details": "паста", "address": "Oudegracht 1", "url": "https://x.nl"}
        self.assertEqual(bot.idea_details(idea), "паста · 📍 Oudegracht 1\n🔗 https://x.nl")
        self.assertEqual(bot.idea_details({"details": "Слепить чашку"}), "Слепить чашку")


if __name__ == "__main__":
    unittest.main()
