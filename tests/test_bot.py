import asyncio
import html
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from girls_agent import bot, weather
from girls_agent.db import Database


def plain(text: str) -> str:
    """An HTML reply as the user reads it: tags gone, entities decoded."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


class RequestedPollTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def fake_message(self, poll_id="p-custom"):
        sent = SimpleNamespace(poll=SimpleNamespace(id=poll_id), message_id=8)
        chat = SimpleNamespace(id=-100, send_poll=AsyncMock(return_value=sent))
        return SimpleNamespace(chat=chat, message_id=7, reply_text=AsyncMock())

    def test_creates_poll_with_clean_options(self):
        message = self.fake_message()
        asyncio.run(bot.create_requested_poll(message, self.db, {
            "question": "Куда идём?", "options": ["Кино", " кино ", "Кино", "Бар", ""], "multiple": True}))
        kwargs = message.chat.send_poll.call_args.kwargs
        self.assertEqual(kwargs["question"], "Куда идём?")
        self.assertEqual(kwargs["options"], ["Кино", "кино", "Бар"])
        self.assertTrue(kwargs["allows_multiple_answers"])
        self.assertEqual(kwargs["reply_to_message_id"], 7)

    def test_needs_two_options(self):
        message = self.fake_message()
        asyncio.run(bot.create_requested_poll(message, self.db, {"question": "?", "options": ["Кино"]}))
        message.chat.send_poll.assert_not_called()
        message.reply_text.assert_called_once()

    def test_close_picks_the_requested_poll_not_an_old_date_poll(self):
        # feedback: a date poll for November was still open; a custom poll about January was closed
        # with /close, and Gigi announced "Встречаемся сб, 7 ноября"
        later = datetime.now(timezone.utc) + timedelta(days=3)
        self.db.add_poll("p-date", -100, 1, "date", ["сб, 7 ноября", "Не могу"],
                         {"dates": ["2026-11-07"]}, later)
        self.db.record_vote("p-date", 5, [0])
        asyncio.run(bot.create_requested_poll(self.fake_message(), self.db, {
            "question": "Когда в январе?", "options": ["16 января", "23 января"]}))
        self.db.record_vote("p-custom", 1, [1])

        poll = self.db.open_polls(-100)[0]
        self.assertEqual(poll["poll_id"], "p-custom")
        tg = SimpleNamespace(stop_poll=AsyncMock(), send_message=AsyncMock())
        asyncio.run(bot.finish_poll(tg, self.db, None, poll))

        text = plain(tg.send_message.call_args.args[1])
        self.assertIn("23 января", text)
        self.assertIn("голосов: 1", text)
        self.assertNotIn("ноябр", text)
        self.assertEqual(tg.send_message.call_count, 1)  # no activity poll after a custom one
        self.assertEqual([p["poll_id"] for p in self.db.open_polls(-100)], ["p-date"])

    def test_requested_poll_without_votes(self):
        asyncio.run(bot.create_requested_poll(self.fake_message(), self.db, {
            "question": "Кино?", "options": ["Да", "Нет"]}))
        tg = SimpleNamespace(stop_poll=AsyncMock(), send_message=AsyncMock())
        asyncio.run(bot.finish_poll(tg, self.db, None, self.db.get_poll("p-custom")))
        self.assertIn("голосов не было", tg.send_message.call_args.args[1])

    def test_parse_date(self):
        self.assertEqual(bot._parse_date("2026-10-31", date(2026, 1, 1)), date(2026, 10, 31))
        self.assertEqual(bot._parse_date("завтра", date(2026, 1, 1)), date(2026, 1, 1))
        self.assertEqual(bot._parse_date(None, date(2026, 1, 1)), date(2026, 1, 1))


class SplitDatePollTest(unittest.TestCase):
    """Fridays and weekends don't fit in one poll: two polls are posted and count as one vote."""

    def setUp(self):
        self.db = Database(":memory:")
        self.cfg = SimpleNamespace(tz=ZoneInfo("Europe/Amsterdam"), meeting_weekdays=(4, 5, 6), date_poll_days=3)
        sent = [SimpleNamespace(poll=SimpleNamespace(id=f"p{i}"), message_id=10 + i) for i in (1, 2)]
        self.tg = SimpleNamespace(send_poll=AsyncMock(side_effect=sent), send_message=AsyncMock(),
                                  stop_poll=AsyncMock(),
                                  get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))
        with patch.object(bot, "datetime", wraps=datetime) as dt:
            dt.now.side_effect = lambda tz=None: datetime(2026, 11, 20, 11, tzinfo=tz or timezone.utc)
            asyncio.run(bot.start_date_poll(self.tg, self.db, self.cfg, -100))

    def test_december_is_split_in_two_polls(self):
        calls = self.tg.send_poll.call_args_list
        self.assertEqual(len(calls), 2)
        first, second = (c.kwargs["options"] for c in calls)
        self.assertEqual((len(first), len(second)), (7, 7))  # 12 Fri/Sat/Sun in December 2026 + "none" each
        self.assertEqual(first[0], "пт, 4 декабря")
        self.assertIn("часть 2 из 2", calls[1].kwargs["question"])

    def test_votes_from_both_parts_count_together(self):
        self.db.record_vote("p1", 1, [0])
        self.db.record_vote("p2", 1, [2])
        self.db.record_vote("p2", 2, [2])
        with patch.object(bot, "propose_activities", AsyncMock()):
            asyncio.run(bot.finish_poll(self.tg, self.db, self.cfg, self.db.get_poll("p1")))
        self.assertEqual(self.tg.stop_poll.call_count, 2)
        self.assertEqual(self.db.open_polls(-100), [])
        part2 = self.db.get_poll("p2")
        winner = bot.json.loads(part2["payload"])["dates"][2]
        self.assertTrue(self.db.has_meeting_in_month(-100, 2026, 12))
        self.assertIn("голосов: 2", self.tg.send_message.call_args.args[1])
        self.assertIn(bot.human_date(winner), plain(self.tg.send_message.call_args.args[1]))

    def test_one_reminder_counts_votes_in_either_part(self):
        self.db.upsert_member(-100, 1, "Аня")
        self.db.upsert_member(-100, 2, "Маша")
        self.db.record_vote("p2", 1, [0])
        self.tg.send_message.reset_mock()
        context = SimpleNamespace(bot=self.tg, bot_data={"db": self.db})
        with patch.object(bot, "_db", return_value=self.db):
            asyncio.run(bot.reminder_job(context))
        self.assertEqual(self.tg.send_message.call_count, 1)
        text = self.tg.send_message.call_args.args[1]
        self.assertIn("Маша", text)
        self.assertNotIn("Аня", text)
        self.assertEqual(self.tg.send_message.call_args.kwargs["reply_to_message_id"], 11)


class LeftMembersTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.upsert_member(-100, 1, "Таня")
        self.db.upsert_member(-100, 2, "Алёна")

    def test_poll_reminder_skips_and_forgets_who_left(self):
        # feedback: the date reminder tagged Alena, who had already left the group
        self.db.add_poll("p", -100, 5, "date", ["сб", "Не могу"], {"dates": ["2026-12-05"]},
                         datetime.now(timezone.utc) + timedelta(days=1))
        tg = SimpleNamespace(send_message=AsyncMock(), get_chat_member=AsyncMock(
            side_effect=lambda chat_id, user_id: SimpleNamespace(status="left" if user_id == 2 else "member")))
        asyncio.run(bot.remind(tg, self.db, self.db.get_poll("p"), quiet_if_done=True))
        text = plain(tg.send_message.call_args.args[1])
        self.assertIn("Таня", text)
        self.assertNotIn("Алёна", text)
        self.assertEqual([m["user_id"] for m in self.db.members(-100)], [1])

    def test_leaving_message_forgets_the_member(self):
        chat = SimpleNamespace(id=-100, type="supergroup", title="Девочки", PRIVATE="private")
        left = SimpleNamespace(id=2)
        update = SimpleNamespace(effective_chat=chat, effective_user=SimpleNamespace(id=2, is_bot=False),
                                 effective_message=SimpleNamespace(left_chat_member=left))
        asyncio.run(bot.track_chat(update, SimpleNamespace(bot_data={"db": self.db})))
        self.assertEqual([m["user_id"] for m in self.db.members(-100)], [1])


class WeatherLookupTest(unittest.TestCase):
    def test_too_far_ahead(self):
        text = asyncio.run(weather.lookup(None, None, date(2026, 12, 31), date(2026, 10, 27)))
        self.assertIn("15", text)


if __name__ == "__main__":
    unittest.main()


class WhitelistTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.upsert_member(-100, 1, "Аня")

    def update(self, chat_id, chat_type, user_id=2):
        chat = SimpleNamespace(id=chat_id, type=chat_type, PRIVATE="private")
        return SimpleNamespace(effective_chat=chat, effective_user=SimpleNamespace(id=user_id))

    def allowed(self, update, chats=(-100,)):
        return bot.is_allowed(update, self.db, SimpleNamespace(allowed_chats=chats))

    def test_no_whitelist_allows_everyone(self):
        self.assertTrue(self.allowed(self.update(-999, "group"), chats=()))

    def test_groups(self):
        self.assertTrue(self.allowed(self.update(-100, "supergroup")))
        self.assertFalse(self.allowed(self.update(-999, "supergroup")))

    def test_private_only_for_members(self):
        self.assertTrue(self.allowed(self.update(1, "private", user_id=1)))
        self.assertFalse(self.allowed(self.update(2, "private", user_id=2)))

    def test_poll_answers_pass(self):
        self.assertTrue(self.allowed(SimpleNamespace(effective_chat=None, effective_user=None)))

    def test_scheduled_messages_skip_other_chats(self):
        self.db.upsert_chat(-100, "Наши")
        self.db.upsert_chat(-999, "Чужие")
        self.assertEqual(bot.group_chats(self.db, SimpleNamespace(allowed_chats=(-100,))), [-100])


class PlansTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.today = date(2026, 10, 28)

    def fake_message(self):
        return SimpleNamespace(chat_id=-100, from_user=SimpleNamespace(full_name="Таня", username=None, id=1),
                               reply_text=AsyncMock())

    def save(self, args, raw=False):
        message = self.fake_message()
        asyncio.run(bot.save_requested_plan(message, self.db, args, self.today))
        reply = message.reply_text.call_args.args[0]
        return reply if raw else plain(reply)

    def test_saves_and_confirms(self):
        reply = self.save({"date": "2026-10-31", "time": "19:00", "what": "ужин", "place": "кафе X", "who": "Таня, Аня"})
        self.assertIn("сб, 31 октября, 19:00 — ужин, кафе X (Таня, Аня)", reply)
        plan = self.db.upcoming_plans(-100, "2026-10-28")[0]
        self.assertEqual((plan["what"], plan["added_by"]), ("ужин", "Таня"))

    def test_place_links_to_maps(self):
        reply = self.save({"date": "2026-10-31", "what": "ужин", "place": "Café <De Pijp>", "who": "Таня"}, raw=True)
        self.assertIn('ужин, <a href="https://www.google.com/maps/search/?api=1&amp;query=Caf%C3%A9+%3CDe+Pijp%3E">'
                      'Café &lt;De Pijp&gt;</a> (Таня)', reply)
        plan = self.db.upcoming_plans(-100, "2026-10-28")[0]
        self.assertEqual(bot.plan_line(plan), "сб, 31 октября — ужин, Café <De Pijp> (Таня)")

    def test_self_becomes_author_name(self):
        self.assertIn("ужин, Moeders (Таня)", self.save({"date": "2026-10-31", "what": "ужин", "place": "Moeders", "who": "я"}))
        self.assertEqual(bot.name_people("я, Аня", "Таня"), "Таня, Аня")
        self.assertEqual(bot.name_people("мы с Аней", "Таня"), "Таня, Аней")
        self.assertEqual(bot.name_people("Я и Таня", "Таня"), "Таня")
        self.assertEqual(bot.name_people("Аня и её муж", "Таня"), "Аня и её муж")
        self.assertEqual(bot.name_people("", "Таня"), "")

    def test_bad_time_is_dropped(self):
        self.save({"date": "2026-10-31", "time": "вечером", "what": "кино"})
        self.assertIsNone(self.db.upcoming_plans(-100, "2026-10-28")[0]["time"])

    def test_rejects_past_and_unparsable_dates(self):
        self.save({"date": "2026-10-01", "what": "кино"})
        self.save({"date": "в субботу", "what": "кино"})
        self.assertEqual(self.db.upcoming_plans(-100, "2000-01-01"), [])

    def test_plans_for_llm_merges_meetups_and_plans_by_date(self):
        self.db.add_plan(-100, "2026-11-07", "йога")
        meeting = self.db.add_meeting(-100, "2026-11-01")
        self.db.set_meeting_plan(meeting, "боулинг", "")
        self.db.add_plan(-100, "2026-10-20", "прошлое")
        lines = bot.plans_for_llm(self.db, -100, self.today).splitlines()
        self.assertEqual(lines, ["[общая встреча, без номера] вс, 1 ноября — боулинг", "[#1] сб, 7 ноября — йога"])


class PlanEditsTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.today = date(2026, 10, 28)
        self.dinner = self.db.add_plan(-100, "2026-10-31", "ужин", time="19:00", place="Moeders")

    def run_reply(self, coro_fn, *args, chat_id=-100):
        message = SimpleNamespace(chat_id=chat_id, from_user=SimpleNamespace(full_name="Таня", username=None, id=1),
                                  reply_text=AsyncMock())
        asyncio.run(coro_fn(message, self.db, *args))
        return plain(message.reply_text.call_args.args[0])

    def test_clash_is_reported_but_saved(self):
        reply = self.run_reply(bot.save_requested_plan, {"date": "2026-10-31", "time": "20:00", "what": "бар"}, self.today)
        self.assertIn("уже записано: сб, 31 октября, 19:00 — ужин, Moeders", reply)
        self.assertEqual(len(self.db.upcoming_plans(-100, "2026-10-28")), 2)

    def test_no_clash_far_apart_or_other_day(self):
        for args in ({"date": "2026-10-31", "time": "12:00", "what": "бранч"}, {"date": "2026-11-01", "what": "кино"}):
            self.assertNotIn("уже записано", self.run_reply(bot.save_requested_plan, args, self.today))

    def test_update_changes_only_given_fields(self):
        reply = self.run_reply(bot.update_requested_plan, {"id": self.dinner, "time": "20:30", "place": ""}, self.today)
        self.assertIn("сб, 31 октября, 20:30 — ужин, Moeders", reply)

    def test_delete(self):
        reply = self.run_reply(bot.delete_requested_plan, f"#{self.dinner}")
        self.assertIn("Удалила", reply)
        self.assertEqual(self.db.upcoming_plans(-100, "2026-10-28"), [])

    def test_other_chats_plans_are_off_limits(self):
        self.assertIn("Не нашла", self.run_reply(bot.delete_requested_plan, self.dinner, chat_id=-999))
        self.assertIn("Не нашла", self.run_reply(bot.update_requested_plan, {"id": self.dinner, "time": "10:00"},
                                                 self.today, chat_id=-999))
        self.assertEqual(len(self.db.upcoming_plans(-100, "2026-10-28")), 1)

    def test_old_plans_are_forgotten(self):
        self.db.add_plan(-100, "2026-10-01", "старое")
        self.assertEqual(self.db.delete_plans_before("2026-10-21"), 1)
        self.assertEqual(len(self.db.upcoming_plans(-100, "2000-01-01")), 1)

    def test_llm_sees_plan_ids(self):
        self.assertIn(f"[#{self.dinner}] сб, 31 октября", bot.plans_for_llm(self.db, -100, self.today))


class IdeasAndRecommendationsTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def reply(self, fn, *args, chat_id=-100):
        message = SimpleNamespace(chat_id=chat_id, from_user=SimpleNamespace(full_name="Таня", username=None, id=1),
                                  reply_text=AsyncMock())
        asyncio.run(fn(message, self.db, *args))
        return message.reply_text.call_args.args[0]

    def test_idea_saved_with_undo_hint(self):
        reply = self.reply(bot.save_requested_idea, {"kind": "ресторан", "title": "De Kas", "details": "оранжерея"})
        idea = self.db.unused_ideas(-100)[0]
        self.assertEqual((idea["kind"], idea["title"], idea["added_by"]), ("ресторан", "De Kas", "Таня"))
        self.assertIn(f"/unidea {idea['id']}", reply)

    def test_unknown_kind_and_category_fall_back(self):
        self.reply(bot.save_requested_idea, {"kind": "бар", "title": "X"})
        self.reply(bot.save_requested_recommendation, {"category": "техника", "title": "Y"})
        self.assertEqual(self.db.unused_ideas(-100)[0]["kind"], "идея")
        self.assertEqual(self.db.recommendations(-100)[0]["category"], "другое")

    def test_film_is_a_recommendation(self):
        reply = self.reply(bot.save_requested_recommendation, {"category": "фильмы и сериалы", "title": "The Invite"})
        self.assertIn("🎬", reply)
        self.assertNotIn("досуг", reply)
        self.assertEqual(self.db.recommendations(-100)[0]["category"], "фильмы и сериалы")

    def test_recommendations_listed_by_category(self):
        self.reply(bot.save_requested_recommendation, {"category": "косметика", "title": "Cetaphil", "recommended_by": "Аня"})
        self.reply(bot.save_requested_recommendation, {"category": "бельё", "title": "Hunkemöller"})
        text = bot.recommendations_text(self.db, -100, "косметика")
        self.assertIn("Cetaphil", text)
        self.assertIn("(Аня)", text)
        self.assertNotIn("Hunkemöller", text)
        self.assertIn("Hunkemöller", bot.recommendations_text(self.db, -100))
        self.assertIn("пока нет", bot.recommendations_text(self.db, -999))

    def test_forget_by_name(self):
        self.db.add_idea(-100, "ресторан", "Караоке-бар Pijp")
        self.assertIn("Удалила идею", self.reply(bot.forget_requested, {"what": "идея", "title": "караоке"}))
        self.assertEqual(self.db.unused_ideas(-100), [])

    def test_forget_ambiguous_lists_numbers(self):
        a = self.db.add_idea(-100, "ресторан", "Bar Botanique")
        b = self.db.add_idea(-100, "ресторан", "Bar Bukowski")
        reply = self.reply(bot.forget_requested, {"what": "идея", "title": "bar"})
        self.assertIn(f"/unidea {a}", reply)
        self.assertIn(f"/unidea {b}", reply)
        self.assertEqual(len(self.db.unused_ideas(-100)), 2)

    def test_other_chats_are_off_limits(self):
        rec = self.db.add_recommendation(-100, "косметика", "Cetaphil")
        self.assertIsNone(self.db.delete_recommendation(-999, rec))
        self.assertIn("Не нашла", self.reply(bot.forget_requested, {"what": "совет", "title": "Cetaphil"}, chat_id=-999))
        self.assertEqual(len(self.db.recommendations(-100)), 1)


class FactsTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def reply(self, fn, arg, user_id=1, chat_id=-100):
        message = SimpleNamespace(chat_id=chat_id, from_user=SimpleNamespace(full_name="Таня", username=None, id=user_id),
                                  reply_text=AsyncMock())
        asyncio.run(fn(message, self.db, arg))
        return message.reply_text.call_args.args[0]

    def test_remember_and_forget_by_wording(self):
        self.assertIn("забудь", self.reply(bot.remember_requested, "не любит суши"))
        self.assertEqual([f["fact"] for f in self.db.facts(-100, 1)], ["не любит суши"])
        self.assertIn("Забыла", self.reply(bot.forget_fact_requested, self.db.facts(-100, 1)[0]["id"]))
        self.assertEqual(self.db.facts(-100, 1), [])

    def test_facts_are_per_person_and_chat(self):
        self.reply(bot.remember_requested, "не ест мясо", user_id=1)
        self.assertEqual(self.db.facts(-100, 2), [])
        self.assertEqual(self.db.facts(-999, 1), [])
        fact_id = self.db.facts(-100, 1)[0]["id"]
        self.assertIsNone(self.db.delete_fact(-100, 2, fact_id))  # someone else can't delete it
        self.assertIn("ничего не помню", self.reply(bot.forget_fact_requested, fact_id, user_id=2))
        self.assertEqual(len(self.db.facts(-100, 1)), 1)

    def test_forget_unknown_lists_what_is_remembered(self):
        self.reply(bot.remember_requested, "не любит суши")
        self.assertIn("Вот что я помню", self.reply(bot.forget_fact_requested, 999))

    def test_limit(self):
        for i in range(bot.MAX_FACTS):
            self.db.add_fact(-100, 1, f"факт {i}")
        self.assertIn("пора что-то забыть", self.reply(bot.remember_requested, "ещё один"))
