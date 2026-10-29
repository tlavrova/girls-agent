import asyncio
import json
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from girls_agent import bot, llm, search, suggest, weather
from girls_agent.db import Database

RESULTS = "1. Trattoria X — https://x.nl\nOudegracht 1\n\n2. Club Y — https://y.nl/events\nGlitterbal, 14 November"
CFG = SimpleNamespace(llm_api_key="k", search_api_key="tvly", llm_base_url="", llm_models=("m",),
                      search_daily_limit=30, meetup_city="Amsterdam", group_file="/nonexistent",
                      weather_city="Амстердам", tz=None)


def answer(*places):
    return "```json\n" + json.dumps(list(places), ensure_ascii=False) + "\n```"


class ParseTest(unittest.TestCase):
    def test_keeps_places_from_results(self):
        places = suggest.parse(answer(
            {"kind": "ресторан", "title": "Trattoria X", "details": "паста", "address": "Oudegracht 1", "url": "https://x.nl"},
            {"kind": "клуб", "title": "Glitterbal — Club Y", "url": "https://y.nl/events"},
        ), RESULTS)
        self.assertEqual([p["title"] for p in places], ["Trattoria X", "Glitterbal — Club Y"])
        self.assertEqual(places[0]["address"], "Oudegracht 1")
        self.assertEqual(places[1]["kind"], "мероприятие")  # unknown kinds become events
        city_only = {"kind": "ресторан", "title": "Trattoria X", "address": "Amsterdam", "url": "https://x.nl"}
        self.assertEqual(suggest.parse(answer(city_only), RESULTS)[0]["address"], "")

    def test_drops_made_up_links_and_junk(self):
        self.assertEqual(suggest.parse(answer({"title": "Herenzicht", "url": "https://herenzicht.nl"}), RESULTS), [])
        self.assertEqual(suggest.parse(answer({"title": "", "url": "https://x.nl"}), RESULTS), [])
        venue_only = {"kind": "вечеринка", "title": "Club Y", "url": "https://y.nl/events"}
        self.assertEqual(suggest.parse(answer(venue_only), RESULTS), [])
        self.assertFalse(suggest.named_event("Club Y — Club Y", RESULTS))
        self.assertFalse(suggest.named_event("Made Up Night — Club Y", RESULTS))
        self.assertTrue(suggest.named_event("Glitterbal - Club Y", RESULTS))
        self.assertEqual(suggest.parse("не JSON", RESULTS), [])
        self.assertEqual(suggest.parse("[{oops", RESULTS), [])

    def test_one_of_each_kind_first(self):
        places = [{"kind": k, "title": t} for k, t in
                  [("ресторан", "A"), ("ресторан", "B"), ("ресторан", "C"), ("мероприятие", "D"), ("вечеринка", "E")]]
        self.assertEqual([p["title"] for p in suggest.diverse(places, 3)], ["A", "D", "E"])
        self.assertEqual([p["title"] for p in suggest.diverse(places[:3], 2)], ["A", "B"])

    def test_queries_mention_the_date(self):
        self.assertIn("14 november 2026", suggest.queries("Amsterdam", date(2026, 11, 14))[1])


class RealPlacesTest(unittest.TestCase):
    def run_places(self, found, reply, n=3, cfg=CFG):
        with patch.object(search, "lookup", AsyncMock(return_value=found)) as lookup, \
                patch.object(weather, "lookup", AsyncMock(return_value="🌧 дождь")), \
                patch.object(llm, "complete", AsyncMock(return_value=reply)) as complete:
            places = asyncio.run(suggest.real_places(cfg, datetime(2026, 10, 29, 11), date(2026, 11, 14), n,
                                                     exclusions=["суши"], past=["Караоке"]))
        return places, lookup, complete

    def test_prompt_has_weather_facts_and_past(self):
        places, lookup, complete = self.run_places(RESULTS, answer({"kind": "ресторан", "title": "Trattoria X",
                                                                    "url": "https://x.nl"}))
        self.assertEqual([p["title"] for p in places], ["Trattoria X"])
        self.assertEqual(lookup.call_count, 2)
        prompt = complete.call_args.args[3][-1]["content"]
        for part in ("сб, 14 ноября", "🌧 дождь", "суши", "Караоке", "до 5"):
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

    def test_real_places_first_saved_ideas_only_as_fallback(self):
        self.db.add_idea(-100, "ресторан", "De Kas")
        self.db.add_exclusion(-100, "караоке")
        real = [{"kind": "мероприятие", "title": "Club Y", "details": "", "address": "", "url": "https://y.nl"},
                {"kind": "ресторан", "title": "Trattoria X", "details": "", "address": "", "url": "https://x.nl"}]
        ideas, real_places = self.ideas(real)
        self.assertEqual([i["title"] for i in ideas], ["Club Y", "Trattoria X", "De Kas"])
        self.assertEqual(real_places.call_args.args[3], 3)
        self.assertEqual(real_places.call_args.kwargs["exclusions"], ["караоке"])

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


class ExclusionsTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.message = SimpleNamespace(chat_id=-100, from_user=SimpleNamespace(full_name="Таня", username=None, id=1),
                                       reply_text=AsyncMock())

    def test_exclude_and_allow_again(self):
        asyncio.run(bot.exclude_requested(self.message, self.db, " караоке "))
        self.assertIn("Больше не предлагаю 🚫 караоке", self.message.reply_text.call_args.args[0])
        row = self.db.exclusions(-100)[0]
        self.assertEqual((row["what"], row["added_by"]), ("караоке", "Таня"))
        self.assertIn("Не нашла", bot.allow_again_text(self.db, -999, row["id"]))  # another chat's rule
        self.assertIn("Снова можно предлагать ✅ караоке", bot.allow_again_text(self.db, -100, row["id"]))
        self.assertEqual(self.db.exclusions(-100), [])

    def test_exclusions_go_into_the_prompt(self):
        system = llm.build_messages("?", datetime(2026, 10, 29), exclusions=["[#2] караоке"])[0]["content"]
        self.assertIn("Не предлагать (правило группы", system)
        self.assertIn("- [#2] караоке", system)
        self.assertNotIn("Не предлагать (", llm.build_messages("?", datetime(2026, 10, 29))[0]["content"])
