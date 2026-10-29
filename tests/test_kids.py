import asyncio
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from girls_agent import kids, llm, search


class WeekendTest(unittest.TestCase):
    def test_coming_weekend(self):
        self.assertEqual(kids.weekend(date(2026, 10, 30)), (date(2026, 10, 31), date(2026, 11, 1)))  # Friday
        self.assertEqual(kids.weekend(date(2026, 10, 31)), (date(2026, 10, 31), date(2026, 11, 1)))  # Saturday
        self.assertEqual(kids.weekend(date(2026, 11, 1)), (date(2026, 10, 31), date(2026, 11, 1)))   # Sunday

    def test_queries(self):
        dutch, english = kids.queries("Amsterdam", date(2026, 10, 31))
        self.assertEqual(dutch, "uitjes met kinderen Amsterdam weekend 31 oktober 2026")
        self.assertIn("Amsterdam", english)


class ComposeTest(unittest.TestCase):
    cfg = SimpleNamespace(llm_api_key="k", search_api_key="tvly", llm_base_url="", llm_models=("m",),
                          search_daily_limit=30, kids_picks_city="Amsterdam", group_file="/nonexistent",
                          weather_city="Амстердаме")
    now = datetime(2026, 10, 30, 12, 30)

    def compose(self, found, answer, cfg=None):
        with patch.object(search, "lookup", AsyncMock(return_value=found)) as lookup, \
                patch.object(llm, "complete", AsyncMock(return_value=answer)) as complete:
            text = asyncio.run(kids.compose(cfg or self.cfg, self.now))
        return text, lookup, complete

    def test_picks_from_results(self):
        text, lookup, complete = self.compose("1. Kinderboerderij — https://x.nl", "👧 Куда с детьми на выходных\n…")
        self.assertEqual(text, "👧 Куда с детьми на выходных\n…")
        self.assertEqual(lookup.call_count, 2)
        prompt = complete.call_args.args[3][-1]["content"]
        self.assertIn("сб, 31 октября", prompt)
        self.assertIn("https://x.nl", prompt)

    def test_nothing_suitable_sends_nothing(self):
        self.assertEqual(self.compose("1. X — https://x.nl", "НИЧЕГО")[0], "")

    def test_no_results_skip_the_llm(self):
        text, _, complete = self.compose("Поиск сейчас не работает", "что-то")
        self.assertEqual(text, "")
        complete.assert_not_called()

    def test_needs_both_keys(self):
        text, lookup, _ = self.compose("1. X — https://x.nl", "что-то", cfg=SimpleNamespace(**{**vars(self.cfg),
                                                                                                "search_api_key": None}))
        self.assertEqual(text, "")
        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
