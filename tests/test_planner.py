import random
import unittest
from datetime import date, datetime, timedelta, timezone

from girls_agent import planner
from girls_agent.db import Database
from girls_agent.seed_ideas import SEED_IDEAS


class PlannerTest(unittest.TestCase):
    def test_candidate_dates_next_month_weekends(self):
        dates = planner.candidate_dates(date(2026, 10, 26), weekdays=(5, 6))
        self.assertEqual([d.day for d in dates], [1, 7, 8, 14, 15, 21, 22, 28, 29])
        self.assertTrue(all(d.weekday() >= 5 for d in dates))
        self.assertTrue(all(d.month == 11 for d in dates))

    def test_candidate_dates_year_rollover_and_limit(self):
        dates = planner.candidate_dates(date(2026, 12, 3), weekdays=range(7), limit=9)
        self.assertEqual(len(dates), 9)
        self.assertEqual(dates[0], date(2027, 1, 1))

    def test_format_date(self):
        self.assertEqual(planner.format_date(date(2026, 11, 6)), "пт, 6 ноября")

    def test_tally_and_winner(self):
        counts = planner.tally([[0, 2], [2], [1, 2], [5]], n_options=3)
        self.assertEqual(counts, [1, 1, 3])
        self.assertEqual(planner.pick_winner(counts), 2)
        self.assertEqual(planner.pick_winner([2, 2, 1]), 0)  # ties go to the earlier date
        self.assertIsNone(planner.pick_winner([0, 0]))

    def test_pick_ideas_prefers_own_and_diversity(self):
        own = [{"kind": "ресторан", "title": "Сыроварня", "details": ""}]
        picked = planner.pick_ideas(own, SEED_IDEAS, n=3, rng=random.Random(1))
        self.assertEqual(len(picked), 3)
        self.assertEqual(picked[0]["title"], "Сыроварня")
        self.assertEqual(len({p["kind"] for p in picked}), 3)

    def test_parse_idea(self):
        self.assertEqual(planner.parse_idea("Ресторан: Сыроварня"), ("ресторан", "Сыроварня"))
        self.assertEqual(planner.parse_idea("сходить на каток"), ("идея", "сходить на каток"))


class DatabaseTest(unittest.TestCase):
    def test_poll_lifecycle(self):
        db = Database(":memory:")
        db.upsert_chat(1, "Девочки")
        db.upsert_member(1, 10, "Аня")
        db.upsert_member(1, 11, "Маша")
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.add_poll("p1", 1, 100, "date", ["a", "b"], {"dates": ["2026-11-06"]}, past)

        db.record_vote("p1", 10, [0])
        self.assertEqual(db.voter_ids("p1"), {10})
        db.record_vote("p1", 10, [])  # vote retracted
        self.assertEqual(db.voter_ids("p1"), set())

        self.assertEqual(len(db.due_polls(datetime.now(timezone.utc))), 1)
        self.assertTrue(db.close_poll("p1"))
        self.assertFalse(db.close_poll("p1"))
        self.assertEqual(db.open_polls(), [])

    def test_meetings_agenda(self):
        db = Database(":memory:")
        mid = db.add_meeting(1, "2026-11-06")
        self.assertTrue(db.has_meeting_in_month(1, 2026, 11))
        db.set_meeting_plan(mid, "Караоке", "")
        self.assertEqual(db.upcoming_meetings(1, "2026-10-26")[0]["activity"], "Караоке")
        db.cancel_meeting(mid)
        self.assertFalse(db.has_meeting_in_month(1, 2026, 11))


if __name__ == "__main__":
    unittest.main()
