"""Gigi (Girls Agent): a Telegram bot that organizes meetups for a group of friends.

MVP flow:
1. On the 20th of the month the bot posts a poll with next month's weekend dates
   (unless a meetup for next month is already scheduled).
2. Every evening it pings members who haven't voted yet.
3. When the poll closes it fixes the date and offers 2-3 activity options in a second poll.
4. The winning option is saved to the shared agenda (/agenda).

Messages to users are in Russian: the bot serves a Russian-speaking group.
"""
from __future__ import annotations

import asyncio
import functools
import html
import json
import logging
import random
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Optional
from urllib.parse import quote_plus

from telegram import Bot, LinkPreviewOptions, Update
from telegram.constants import ChatAction, MessageEntityType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ApplicationHandlerStop,
    ContextTypes,
    Defaults,
    MessageHandler,
    PollAnswerHandler,
    TypeHandler,
    filters,
)

from . import llm, morning, planner, search, suggest, weather
from .config import Config, load_config
from .db import Database
from .goodnight import GOODNIGHT
from .seed_ideas import SEED_IDEAS

log = logging.getLogger("girls_agent")

NONE_OPTION = "Ни одна не подходит 😢"

BOT_NAME = "Gigi 💖"
# Shown in the bot's profile and before a chat with it starts (Telegram limits: 120 and 512 chars)
SHORT_DESCRIPTION = "Gigi — помогаю подругам собираться: даты, идеи, напоминания и общая повестка 💖"
DESCRIPTION = (
    "Привет, я Gigi! Помогаю вашей компании встречаться чаще и без долгих переписок: "
    "раз в месяц спрашиваю, когда всем удобно, напоминаю тем, кто забыл проголосовать, "
    "предлагаю, куда пойти, и записываю план, чтобы он не потерялся в чате. "
    "Добавьте меня в групповой чат 💌"
)

# Plans are deleted this many days after their date
PLAN_RETENTION_DAYS = 7

# (command, description) for /help and the Telegram command menu
COMMANDS = [
    ("join", "я участвую, напоминай мне"),
    ("leave", "больше не напоминать мне"),
    ("poll", "опрос по датам прямо сейчас"),
    ("remind", "напомнить тем, кто не проголосовал"),
    ("close", "подвести итоги опроса досрочно"),
    ("idea", "сохранить идею: /idea ресторан: Название, адрес"),
    ("ideas", "банк идей"),
    ("unidea", "удалить идею: /unidea 12 (номер из /ideas)"),
    ("recs", "что советовали: косметика, одежда, еда…"),
    ("memory", "что я о тебе помню"),
    ("forget", "забыть факт о тебе: /forget 3 (номер из /memory)"),
    ("unrec", "удалить совет: /unrec 5 (номер из /recs)"),
    ("agenda", "ближайшие встречи и планы"),
    ("cancel", "отменить ближайшую встречу"),
    ("unplan", "удалить план: /unplan 12 (номер из /agenda)"),
]

HELP = (
    "Привет, я <b>Gigi</b> 💖\n"
    "Помогаю нашей компании собираться, чтобы встречи не терялись в переписке.\n\n"
    "<b>Что я делаю</b>\n"
    "🗓 {poll_day}-го числа спрашиваю, в какие выходные следующего месяца всем удобно\n"
    "🔔 Мягко напоминаю тем, кто ещё не проголосовал\n"
    "✨ Когда дата выбрана, предлагаю 3 варианта: ресторан, вечеринка или событие\n"
    "📒 Записываю план в общую повестку и закрепляю его в чате\n"
    "☀️ По утрам — пожелание, главная новость и погода\n"
    "🌙 Каждый вечер желаю всем спокойной ночи\n"
    "💬 Тегни меня или ответь на моё сообщение — поболтаем. Могу сделать опрос, подсказать погоду или запомнить план: «запомни, в субботу в 19:00 идём в кафе X». Пришли мне ресторан или совет (крем, магазин, мастер) — запишу\n\n"
    "<b>Команды</b>\n"
    + "".join(f"/{name} — {text}\n" for name, text in COMMANDS)
    + "\nВиды идей: ресторан, вечеринка, мероприятие."
)


# --- helpers ---

def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.bot_data["db"]


def _cfg(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.bot_data["cfg"]


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def display_name(user) -> str:
    return user.full_name or user.username or str(user.id)


def human_date(iso: str) -> str:
    return planner.format_date(date.fromisoformat(iso))


def maps_url(place: str) -> str:
    """A Google Maps search for the place: finds it by name, so it works without knowing the address."""
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(place)


def plan_line(p, links: bool = False) -> str:
    """One saved plan: "сб, 31 октября, 19:00 — ужин, кафе X (Таня, Аня)".

    links=True gives HTML with the place linked to Google Maps; otherwise plain text.
    """
    esc = html.escape if links else str
    when = human_date(p["date"]) + (f", {p['time']}" if p["time"] else "")
    line = esc(f"{when} — {p['what']}")
    if p["place"]:
        place = f'<a href="{html.escape(maps_url(p["place"]))}">{html.escape(p["place"])}</a>' if links else p["place"]
        line += f", {place}"
    return line + (esc(f" ({p['who']})") if p["who"] else "")


def plans_for_llm(db: Database, chat_id: int, today: date) -> str:
    """Upcoming meetups and saved plans, one per line, so Gigi answers questions about plans with facts."""
    lines = []
    for m in db.upcoming_meetings(chat_id, today.isoformat()):
        lines.append((m["date"], f"[общая встреча, без номера] {human_date(m['date'])} — {m['activity'] or 'досуг ещё выбираем'}"))
    for p in db.upcoming_plans(chat_id, today.isoformat()):
        lines.append((p["date"], f"[#{p['id']}] {plan_line(p)}"))
    return "\n".join(text for _, text in sorted(lines, key=lambda x: x[0]))


# --- meetup flow ---

async def start_date_poll(bot: Bot, db: Database, cfg: Config, chat_id: int) -> None:
    today = datetime.now(cfg.tz).date()
    dates = planner.candidate_dates(today, cfg.meeting_weekdays)
    if len(dates) < 1:
        await bot.send_message(chat_id, "Не нашла подходящих дат — проверьте MEETING_WEEKDAYS в настройках.")
        return
    month_word = planner.month_prepositional(dates[0])
    options = [planner.format_date(d) for d in dates] + [NONE_OPTION]
    msg = await bot.send_poll(
        chat_id,
        question=f"Когда встречаемся в {month_word}? Отметьте все удобные даты 💃",
        options=options,
        is_anonymous=False,  # otherwise the bot can't see who voted and can't remind
        allows_multiple_answers=True,
    )
    closes_at = datetime.now(timezone.utc) + timedelta(days=cfg.date_poll_days)
    db.add_poll(msg.poll.id, chat_id, msg.message_id, "date", options,
                {"dates": [d.isoformat() for d in dates]}, closes_at)
    await bot.send_message(
        chat_id,
        f"Голосуем до {closes_at.astimezone(cfg.tz):%d.%m %H:%M}. "
        "Если вас нет в моём списке для напоминаний — нажмите /join.",
        reply_to_message_id=msg.message_id,
    )


def idea_details(idea: dict) -> str:
    """An idea's details with the address and link of a real place, as plain text for the agenda."""
    text = idea.get("details") or ""
    if idea.get("address"):
        text += f"{' · ' if text else ''}📍 {idea['address']}"
    if idea.get("url"):
        text += f"{chr(10) if text else ''}🔗 {idea['url']}"
    return text


async def meetup_ideas(db: Database, cfg: Config, chat_id: int, meeting_date: str, n: int = 3) -> list:
    """The group's own ideas first, then real places found for that date, then starter ideas."""
    ideas = planner.pick_ideas(db.unused_ideas(chat_id), [], n=n)
    now = datetime.now(cfg.tz)
    ideas += await suggest.real_places(cfg, now, date.fromisoformat(meeting_date), n - len(ideas),
                                       facts=db.chat_facts(chat_id), past=db.past_activities(chat_id, meeting_date))
    titles = {i["title"] for i in ideas}
    ideas += [s for s in planner.pick_ideas([], SEED_IDEAS, n=n) if s["title"] not in titles][:n - len(ideas)]
    return ideas


async def propose_activities(bot: Bot, db: Database, cfg: Config, chat_id: int,
                             meeting_id: int, meeting_date: str) -> None:
    ideas = await meetup_ideas(db, cfg, chat_id, meeting_date)
    options = [planner.idea_option(i) for i in ideas]
    lines = [f"Что делаем {html.escape(human_date(meeting_date))}? Варианты:"]
    for idea in ideas:
        line = f"{planner.KIND_EMOJI.get(idea['kind'], '✨')} <b>{html.escape(idea['title'])}</b>"
        if idea.get("details"):
            line += f" — {html.escape(idea['details'])}"
        if idea.get("added_by"):
            line += f" <i>(идея от {html.escape(idea['added_by'])})</i>"
        links = []
        if idea.get("address"):
            links.append(f'📍 <a href="{html.escape(maps_url(idea["address"]))}">{html.escape(idea["address"])}</a>')
        if idea.get("url"):
            links.append(f'<a href="{html.escape(idea["url"])}">источник</a>')
        if links:
            line += "\n    " + " · ".join(links)
        lines.append(line)
    await bot.send_message(chat_id, "\n".join(lines), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)

    msg = await bot.send_poll(
        chat_id,
        question=planner.truncate(f"Выбираем досуг на {human_date(meeting_date)} 🥂", 300),
        options=options,
        is_anonymous=False,
        allows_multiple_answers=False,
    )
    closes_at = datetime.now(timezone.utc) + timedelta(hours=cfg.activity_poll_hours)
    db.add_poll(msg.poll.id, chat_id, msg.message_id, "activity", options,
                {"meeting_id": meeting_id, "ideas": ideas}, closes_at)


async def finish_poll(bot: Bot, db: Database, cfg: Config, poll) -> None:
    if not db.close_poll(poll["poll_id"]):
        return  # already closed concurrently (command + scheduled job)
    try:
        await bot.stop_poll(poll["chat_id"], poll["message_id"])
    except TelegramError as e:
        log.warning("stop_poll failed: %s", e)

    chat_id = poll["chat_id"]
    options = json.loads(poll["options"])
    payload = json.loads(poll["payload"])
    counts = planner.tally(db.votes(poll["poll_id"]), len(options))

    if poll["kind"] == "date":
        dates = payload["dates"]
        winner = planner.pick_winner(counts[: len(dates)])
        if winner is None:
            await bot.send_message(
                chat_id,
                "Ни одна дата не набрала голосов 😔 Запустите опрос заново — /poll.",
            )
            return
        meeting_date = dates[winner]
        meeting_id = db.add_meeting(chat_id, meeting_date)
        await bot.send_message(
            chat_id,
            f"🗓 Решено! Встречаемся <b>{html.escape(human_date(meeting_date))}</b> "
            f"(голосов: {counts[winner]}).",
            parse_mode=ParseMode.HTML,
        )
        await propose_activities(bot, db, cfg, chat_id, meeting_id, meeting_date)

    elif poll["kind"] == "activity":
        ideas = payload["ideas"]
        winner = planner.pick_winner(counts)
        note = ""
        if winner is None:
            winner = random.randrange(len(ideas))
            note = "Голосов не было — доверилась судьбе 🎲\n"
        idea = ideas[winner]
        meeting = db.get_meeting(payload["meeting_id"])
        db.set_meeting_plan(meeting["id"], idea["title"], idea_details(idea))
        if idea.get("id"):
            db.mark_idea_used(idea["id"])
        msg = await bot.send_message(
            chat_id,
            f"{note}✅ <b>План готов!</b>\n"
            f"🗓 {html.escape(human_date(meeting['date']))}\n"
            f"{planner.KIND_EMOJI.get(idea['kind'], '✨')} {html.escape(idea['title'])}"
            + (f"\n📝 {html.escape(idea_details(idea))}" if idea_details(idea) else "")
            + "\n\nВсё сохранено в /agenda",
            parse_mode=ParseMode.HTML,
        )
        try:  # pin it if the bot is a chat admin
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except TelegramError as e:
            log.info("pin failed in %s: %s", chat_id, e)
            await bot.send_message(
                chat_id,
                "Хотела закрепить план, но у меня нет прав 📌 "
                "Сделайте меня админом с правом «Закреплять сообщения» — и в следующий раз закреплю сама.",
            )


async def remind(bot: Bot, db: Database, poll, *, quiet_if_done: bool) -> None:
    chat_id = poll["chat_id"]
    members = db.members(chat_id)
    if not members:
        if not quiet_if_done:
            await bot.send_message(chat_id, "Я пока никого не знаю 🙈 Нажмите /join, чтобы я могла напоминать.")
        return
    voted = db.voter_ids(poll["poll_id"])
    missing = [m for m in members if m["user_id"] not in voted]
    if not missing:
        if not quiet_if_done:
            await bot.send_message(chat_id, "Все уже проголосовали — вы чудо 💖")
        return
    what = "даты" if poll["kind"] == "date" else "досуг"
    await bot.send_message(
        chat_id,
        f"Девочки, ждём ваш голос за {what} 🙏 " + ", ".join(mention(m["user_id"], m["name"]) for m in missing),
        parse_mode=ParseMode.HTML,
        reply_to_message_id=poll["message_id"],
    )


# --- scheduled jobs ---

async def monthly_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs on POLL_DAY of every month."""
    db, cfg = _db(context), _cfg(context)
    today = datetime.now(cfg.tz).date()
    year, month = planner.next_month(today)
    for chat_id in group_chats(db, cfg):
        if db.open_polls(chat_id) or db.has_meeting_in_month(chat_id, year, month):
            continue
        try:
            await start_date_poll(context.bot, db, cfg, chat_id)
        except TelegramError as e:
            log.warning("monthly poll failed for %s: %s", chat_id, e)


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    for poll in db.open_polls():
        try:
            await remind(context.bot, db, poll, quiet_if_done=True)
        except TelegramError as e:
            log.warning("reminder failed for %s: %s", poll["chat_id"], e)


async def goodnight_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    db, cfg = _db(context), _cfg(context)
    today = datetime.now(cfg.tz).date()
    for chat_id in group_chats(db, cfg):
        try:
            await context.bot.send_message(chat_id, planner.daily_pick(GOODNIGHT, today, seed=chat_id))
        except TelegramError as e:
            log.warning("good-night message failed for %s: %s", chat_id, e)


async def morning_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    db, cfg = _db(context), _cfg(context)
    chat_ids = group_chats(db, cfg)
    if not chat_ids:
        return
    # composed once for all chats: one LLM request a day
    text = await morning.compose(cfg, datetime.now(cfg.tz))
    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id, text)
        except TelegramError as e:
            log.warning("morning message failed for %s: %s", chat_id, e)


async def forget_old_plans_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cutoff = datetime.now(_cfg(context).tz).date() - timedelta(days=PLAN_RETENTION_DAYS)
    removed = _db(context).delete_plans_before(cutoff.isoformat())
    if removed:
        log.info("forgot %d plans from before %s", removed, cutoff)


async def close_due_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    db, cfg = _db(context), _cfg(context)
    for poll in db.due_polls(datetime.now(timezone.utc)):
        try:
            await finish_poll(context.bot, db, cfg, poll)
        except TelegramError as e:
            log.warning("closing poll failed for %s: %s", poll["chat_id"], e)


# --- access ---

def group_chats(db: Database, cfg: Config) -> list:
    """Chats the scheduled messages go to: the known ones, limited to ALLOWED_CHATS if it's set."""
    return [c for c in db.chat_ids() if not cfg.allowed_chats or c in cfg.allowed_chats]


def is_allowed(update: Update, db: Database, cfg: Config) -> bool:
    """Whitelist check: allowed group chats, and private chats only with members of those groups."""
    if not cfg.allowed_chats:
        return True
    chat, user = update.effective_chat, update.effective_user
    if chat is None:
        # poll answers carry no chat; the bot only has polls in allowed chats
        return True
    if chat.id in cfg.allowed_chats:
        return True
    return chat.type == chat.PRIVATE and user is not None and db.is_member(user.id, cfg.allowed_chats)


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs before every handler: strangers get no reply at all, unknown groups are left right away."""
    if is_allowed(update, _db(context), _cfg(context)):
        return
    chat = update.effective_chat
    if chat.type != chat.PRIVATE:
        log.warning("leaving chat %s (%s): not in ALLOWED_CHATS", chat.id, chat.title)
        try:
            await context.bot.leave_chat(chat.id)
        except TelegramError as e:
            log.warning("could not leave chat %s: %s", chat.id, e)
    raise ApplicationHandlerStop


# --- commands ---

async def track_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remember the chat and the author of every message so we know whom to remind."""
    chat, user = update.effective_chat, update.effective_user
    if chat is None or chat.type == chat.PRIVATE:
        return
    db = _db(context)
    db.upsert_chat(chat.id, chat.title)
    if user and not user.is_bot:
        db.upsert_member(chat.id, user.id, display_name(user))


def addresses_gigi(message, bot) -> bool:
    """True if the message mentions the bot or replies to one of its messages."""
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == bot.id:
        return True
    for entity, text in message.parse_entities([MessageEntityType.MENTION, MessageEntityType.TEXT_MENTION]).items():
        if text.lower() == f"@{bot.username}".lower() or (entity.user and entity.user.id == bot.id):
            return True
    return False


async def assistant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, cfg, bot = update.effective_message, _cfg(context), context.bot
    if not addresses_gigi(message, bot):
        return
    question = re.sub(f"@{re.escape(bot.username)}", "", message.text, flags=re.IGNORECASE).strip()
    if not question:
        await message.reply_text("Да-да? 💁‍♀️")
        return

    # per-chat daily limit, kept in memory: a restart simply resets it
    now = datetime.now(cfg.tz)
    counts = context.bot_data.setdefault("llm_counts", {})
    key = (message.chat_id, now.date())
    if counts.get(key, 0) >= cfg.llm_daily_limit:
        await message.reply_text("На сегодня я наболталась 🙄 Завтра продолжим.")
        return
    counts[key] = counts.get(key, 0) + 1

    reply = message.reply_to_message
    replied_text = (reply.text or reply.caption) if reply else None
    messages = llm.build_messages(
        question, now, author=display_name(update.effective_user),
        group=llm.load_group(cfg.group_file, cfg.weather_city),
        plans=plans_for_llm(_db(context), message.chat_id, now.date()),
        facts=[f"[#{f['id']}] {f['fact']}" for f in _db(context).facts(message.chat_id, update.effective_user.id)],
        replied_text=replied_text,
        replied_is_gigi=bool(reply and reply.from_user and reply.from_user.id == bot.id),
        search=bool(cfg.search_api_key),
    )
    typing = asyncio.create_task(keep_typing(bot, message.chat_id))
    try:
        await answer_with_llm(message, _db(context), cfg, messages, now)
    finally:
        typing.cancel()


async def keep_typing(bot: Bot, chat_id: int) -> None:
    """Show "Gigi is typing…" until cancelled; Telegram clears it after about 5 seconds."""
    while True:
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        except TelegramError:
            pass
        await asyncio.sleep(4)


LOOKUPS = ("get_weather", "web_search")  # tools whose result goes back to the model before it answers
MAX_LOOKUP_ROUNDS = 3
MAX_SEARCHES = 2  # per answer: a search and one refined query are enough, and the free tier is small


async def lookup(cfg: Config, name: str, args: dict, today: date, searches: int) -> str:
    if name == "get_weather":
        return await weather.lookup(cfg, args.get("city"), _parse_date(args.get("date"), today), today)
    if searches > MAX_SEARCHES:
        return "Хватит поисков, ответь по тому, что уже нашлось."
    return await search.lookup(cfg.search_api_key, args.get("query"), today, cfg.search_daily_limit)


async def answer_with_llm(message, db: Database, cfg: Config, messages: list, now: datetime) -> None:
    ask = functools.partial(llm.chat, cfg.llm_base_url, cfg.llm_api_key, cfg.llm_models,
                            tools=llm.tools(bool(cfg.search_api_key)))
    reply_msg = await ask(messages)
    # weather and search results go back to the model, which then answers or calls an action tool;
    # every call in the reply gets a result, as the API expects
    fallback, searches = "", 0
    for _ in range(MAX_LOOKUP_ROUNDS):
        calls = llm.tool_calls(reply_msg) if reply_msg else []
        if not calls or any(name not in LOOKUPS for name, _, _ in calls):
            break
        results = []
        for name, args, call_id in calls:
            searches += name == "web_search"
            facts = await lookup(cfg, name, args, now.date(), searches)
            fallback = facts if name == "get_weather" else ""
            results.append({"role": "tool", "tool_call_id": call_id, "content": facts})
        messages = messages + [reply_msg] + results
        reply_msg = await ask(messages)
    call = llm.tool_call(reply_msg) if reply_msg else None
    if call and call[0] == "create_poll":
        await create_requested_poll(message, call[1])
        return
    if call and call[0] == "save_plan":
        await save_requested_plan(message, db, call[1], now.date())
        return
    if call and call[0] == "save_idea":
        await save_requested_idea(message, db, call[1])
        return
    if call and call[0] == "save_recommendation":
        await save_requested_recommendation(message, db, call[1])
        return
    if call and call[0] == "list_recommendations":
        await message.reply_text(recommendations_text(db, message.chat_id, call[1].get("category")),
                                 parse_mode=ParseMode.HTML)
        return
    if call and call[0] == "remember_fact":
        await remember_requested(message, db, call[1].get("fact"))
        return
    if call and call[0] == "forget_fact":
        await forget_fact_requested(message, db, call[1].get("id"))
        return
    if call and call[0] == "forget_saved":
        await forget_requested(message, db, call[1])
        return
    if call and call[0] == "update_plan":
        await update_requested_plan(message, db, call[1], now.date())
        return
    if call and call[0] == "delete_plan":
        await delete_requested_plan(message, db, call[1].get("id"))
        return
    answer = ((reply_msg or {}).get("content") or "").strip() or fallback
    # several found places would each get a link preview: none is cleaner
    await message.reply_text(answer or "Что-то я зависла 😵‍💫 Спроси ещё раз чуть позже.",
                             link_preview_options=NO_PREVIEW if searches else None)


def _parse_date(value, default: date) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return default


async def create_requested_poll(message, args: dict) -> None:
    """A poll asked for in plain language; the LLM already extracted question and options."""
    question = planner.truncate(str(args.get("question") or "Опрос").strip(), 300)
    options = []
    for option in args.get("options") or []:
        option = planner.truncate(str(option).strip(), 100)
        if option and option not in options:
            options.append(option)
    if len(options) < 2:
        await message.reply_text("Для опроса нужно хотя бы два варианта. Перечисли их, и я всё сделаю 💅")
        return
    try:
        await message.chat.send_poll(
            question=question,
            options=options[:10],  # Telegram's limit
            is_anonymous=False,
            allows_multiple_answers=bool(args.get("multiple")),
            reply_to_message_id=message.message_id,
        )
    except TelegramError as e:
        log.warning("requested poll failed: %s", e)
        await message.reply_text("Telegram не дал создать опрос 🙄 Попробуй переформулировать.")


TIME_RE = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")
OVERLAP = timedelta(hours=2)  # plans this close on the same day probably clash
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)  # a map card under every plan is noise
WHO_SPLIT_RE = re.compile(r"\s*(?:,|\bи\b|\bсо?\b)\s*", re.IGNORECASE)
SELF_WORDS = {"я", "меня", "мне", "мной", "мы", "нас"}  # the LLM sometimes keeps these instead of the author's name


def name_people(who: str, author: str) -> str:
    """ "я, Аня" -> "Таня, Аня": in the group's plan list "я" means nothing, so it becomes the author's name.

    Left as is when there's no such word, so "Аня и её муж" isn't mangled.
    """
    parts = [w for w in WHO_SPLIT_RE.split(who) if w]
    if not any(w.lower() in SELF_WORDS for w in parts):
        return who
    names = []
    for w in parts:
        name = author if w.lower() in SELF_WORDS else w
        if name not in names:
            names.append(name)
    return ", ".join(names)


def clean_plan(args: dict, today: date):
    """Validated plan fields from LLM arguments, or (None, a reply explaining what's wrong)."""
    try:
        plan_date = date.fromisoformat(str(args.get("date")))
    except ValueError:
        plan_date = None
    what = planner.truncate(str(args.get("what") or "").strip(), 100)
    if plan_date is None or not what:
        return None, "Не поняла, что и на какой день записать 🤔 Скажи, например: «в субботу в 19:00 ужин в кафе X»."
    if plan_date < today:
        return None, f"{human_date(plan_date.isoformat())} уже прошло. Машину времени мне пока не выдали 🙃"
    time = str(args.get("time") or "").strip()
    return {
        "date": plan_date.isoformat(),
        "time": time if TIME_RE.fullmatch(time) else None,
        "what": what,
        "place": planner.truncate(str(args.get("place") or "").strip(), 100),
        "who": planner.truncate(str(args.get("who") or "").strip(), 100),
    }, ""


def clashes(db: Database, chat_id: int, plan: dict, skip_id: Optional[int] = None) -> list:
    """Saved plans on the same day within OVERLAP of this one; a plan without a time clashes with the whole day."""
    found = []
    for p in db.upcoming_plans(chat_id, plan["date"]):
        if p["date"] != plan["date"] or p["id"] == skip_id:
            continue
        if not p["time"] or not plan["time"] or abs(
                datetime.strptime(p["time"], "%H:%M") - datetime.strptime(plan["time"], "%H:%M")) < OVERLAP:
            found.append(p)
    return found


def clash_note(found: list) -> str:
    if not found:
        return ""
    names = "; ".join(plan_line(p, links=True) for p in found)
    return f"\n⚠️ На это время уже записано: {names}. Оставить оба или заменить? Скажи «@Gigi удали …», если старое не нужно."


async def save_requested_plan(message, db: Database, args: dict, today: date) -> None:
    """A plan asked to be remembered in plain language; the LLM already extracted the fields.

    The confirmation is built here, not by the LLM, so it shows exactly what was saved.
    A clash with another plan is pointed out, not blocked: dinner and then a bar is a normal evening.
    """
    plan, error = clean_plan(args, today)
    if not plan:
        await message.reply_text(error)
        return
    plan["who"] = name_people(plan["who"], display_name(message.from_user))
    found = clashes(db, message.chat_id, plan)
    db.add_plan(message.chat_id, plan["date"], plan["what"], time=plan["time"], place=plan["place"],
                who=plan["who"], added_by=display_name(message.from_user))
    await message.reply_text(f"Записала 📌 {plan_line(plan, links=True)}{clash_note(found)}\nВсе планы — /agenda",
                             parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


async def update_requested_plan(message, db: Database, args: dict, today: date) -> None:
    """Move or fix a saved plan: only the fields the LLM gave change."""
    old = db.get_plan(message.chat_id, _plan_id(args.get("id")))
    if not old:
        await message.reply_text("Не нашла такой план 🤔 Посмотри номера в /agenda.")
        return
    changes = {k: v for k, v in args.items() if k in ("date", "time", "what", "place", "who") and v not in (None, "")}
    plan, error = clean_plan({**dict(old), **changes}, today)
    if not plan:
        await message.reply_text(error)
        return
    plan["who"] = name_people(plan["who"], display_name(message.from_user))
    db.update_plan(message.chat_id, old["id"], plan["date"], plan["what"], time=plan["time"],
                   place=plan["place"], who=plan["who"])
    found = clashes(db, message.chat_id, plan, skip_id=old["id"])
    await message.reply_text(f"Поправила ✏️ {plan_line(plan, links=True)}{clash_note(found)}",
                             parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


async def delete_requested_plan(message, db: Database, plan_id) -> None:
    plan = db.get_plan(message.chat_id, _plan_id(plan_id))
    if not plan or not db.delete_plan(message.chat_id, plan["id"]):
        await message.reply_text("Не нашла такой план 🤔 Посмотри номера в /agenda.")
        return
    await message.reply_text(f"Удалила 🗑 {plan_line(plan)}")


def _plan_id(value) -> int:
    try:
        return int(str(value).lstrip("#"))
    except ValueError:
        return 0


async def save_requested_idea(message, db: Database, args: dict) -> None:
    """A place or event sent to Gigi without /idea; the confirmation says how to undo a wrong guess."""
    kind = str(args.get("kind") or "").strip().lower()
    kind = kind if kind in planner.KIND_EMOJI else "идея"
    title = planner.truncate(str(args.get("title") or "").strip(), 100)
    if not title:
        await message.reply_text("Не поняла, что сохранить 🤔 Напиши название места.")
        return
    details = planner.truncate(str(args.get("details") or "").strip(), 300)
    idea_id = db.add_idea(message.chat_id, kind, title, details, display_name(message.from_user))
    await message.reply_text(f"{planner.KIND_EMOJI[kind]} Сохранила в банк идей: {title}. "
                             f"Предложу, когда будем выбирать досуг.\nНе то? /unidea {idea_id}")


async def save_requested_recommendation(message, db: Database, args: dict) -> None:
    category = str(args.get("category") or "").strip().lower()
    category = category if category in planner.RECOMMENDATION_EMOJI else "другое"
    title = planner.truncate(str(args.get("title") or "").strip(), 100)
    if not title:
        await message.reply_text("Не поняла, что записать 🤔 Напиши, что именно советуешь.")
        return
    details = planner.truncate(str(args.get("details") or "").strip(), 300)
    by = planner.truncate(str(args.get("recommended_by") or "").strip(), 50) or display_name(message.from_user)
    rec_id = db.add_recommendation(message.chat_id, category, title, details, by)
    await message.reply_text(f"{planner.RECOMMENDATION_EMOJI[category]} Записала в советы ({category}): {title}. "
                             f"Спросишь — напомню.\nНе то? /unrec {rec_id}")


def recommendations_text(db: Database, chat_id: int, category: Optional[str] = None) -> str:
    """Recommendations grouped by category, as HTML."""
    category = (category or "").strip().lower()
    category = category if category in planner.RECOMMENDATION_EMOJI else None
    recs = db.recommendations(chat_id, category)
    if not recs:
        where = f" в категории «{category}»" if category else ""
        return f"Советов{where} пока нет. Расскажи, что нравится, — тегни меня, и я запишу 💌"
    lines, current = [], None
    for r in recs:
        if r["category"] != current:
            current = r["category"]
            lines.append(f"\n{planner.RECOMMENDATION_EMOJI.get(current, '💡')} <b>{html.escape(current.capitalize())}</b>")
        line = f"#{r['id']} {html.escape(r['title'])}"
        if r["details"]:
            line += f" — {html.escape(r['details'])}"
        if r["recommended_by"]:
            line += f" <i>({html.escape(r['recommended_by'])})</i>"
        lines.append(line)
    return "\n".join(lines).strip()


async def forget_requested(message, db: Database, args: dict) -> None:
    """Delete an idea or recommendation by name; only an unambiguous match is deleted, otherwise list the numbers."""
    is_idea = str(args.get("what") or "").strip().lower() != "совет"
    query = str(args.get("title") or "").strip().lower()
    rows = db.unused_ideas(message.chat_id) if is_idea else db.recommendations(message.chat_id)
    found = [r for r in rows if query and (query in r["title"].lower() or r["title"].lower() in query)]
    command, listing = ("/unidea", "/ideas") if is_idea else ("/unrec", "/recs")
    if not found:
        await message.reply_text(f"Не нашла такого 🤔 Посмотри в {listing} и удали по номеру: {command} номер")
    elif len(found) > 1:
        options = "\n".join(f"{command} {r['id']} — {r['title']}" for r in found[:10])
        await message.reply_text(f"Нашла несколько, какое удалить?\n{options}")
    else:
        if is_idea:
            db.delete_idea(message.chat_id, found[0]["id"])
        else:
            db.delete_recommendation(message.chat_id, found[0]["id"])
        await message.reply_text(f"Удалила {'идею' if is_idea else 'совет'} 🗑 {found[0]['title']}")


MAX_FACTS = 30  # per person: keeps the prompt short


async def remember_requested(message, db: Database, fact) -> None:
    fact = planner.truncate(str(fact or "").strip(), 150)
    if not fact:
        await message.reply_text("Не поняла, что запомнить 🤔")
        return
    if len(db.facts(message.chat_id, message.from_user.id)) >= MAX_FACTS:
        await message.reply_text("Я уже столько про тебя помню, что пора что-то забыть 🙃 Посмотри /memory.")
        return
    fact_id = db.add_fact(message.chat_id, message.from_user.id, fact)
    await message.reply_text(f"Запомнила 🧠 {fact}\nЧто я о тебе помню — /memory, забыть — /forget {fact_id}")


async def forget_fact_requested(message, db: Database, fact_id) -> None:
    """Forget one of the author's own facts by its number; a wrong number lists what's remembered."""
    fact = db.delete_fact(message.chat_id, message.from_user.id, _plan_id(fact_id))
    if fact:
        await message.reply_text(f"Забыла 🫥 {fact['fact']}")
        return
    facts = db.facts(message.chat_id, message.from_user.id)
    if not facts:
        await message.reply_text("Я про тебя пока ничего не помню 🤷‍♀️")
    else:
        await message.reply_text("Не поняла, что забыть. Вот что я помню:\n"
                                 + "\n".join(f"/forget {f['id']} — {f['fact']}" for f in facts))


async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """What Gigi remembers about you; each person sees only their own facts."""
    facts = _db(context).facts(update.effective_chat.id, update.effective_user.id)
    if not facts:
        await update.effective_message.reply_text(
            "Я про тебя пока ничего не помню. Расскажи: «@Gigi запомни, я не ем мясо» 💌")
        return
    lines = ["🧠 Что я о тебе помню:"] + [f"#{f['id']} {f['fact']}" for f in facts] + ["Забыть: /forget номер"]
    await update.effective_message.reply_text("\n".join(lines))


async def forget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fact = _db(context).delete_fact(update.effective_chat.id, update.effective_user.id,
                                    _plan_id(context.args[0] if context.args else ""))
    await update.effective_message.reply_text(
        f"Забыла 🫥 {fact['fact']}" if fact else "Не нашла такого среди того, что я о тебе помню. Номера — в /memory.")


async def recs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        recommendations_text(_db(context), update.effective_chat.id), parse_mode=ParseMode.HTML)


async def unidea_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    idea = _db(context).delete_idea(update.effective_chat.id, _plan_id(context.args[0] if context.args else ""))
    await update.effective_message.reply_text(
        f"Удалила идею 🗑 {idea['title']}" if idea else "Не нашла такую идею 🤔 Номер есть в /ideas.")


async def unrec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rec = _db(context).delete_recommendation(update.effective_chat.id, _plan_id(context.args[0] if context.args else ""))
    await update.effective_message.reply_text(
        f"Удалила совет 🗑 {rec['title']}" if rec else "Не нашла такой совет 🤔 Номер есть в /recs.")


async def unplan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unplan 12: delete a saved plan without the LLM."""
    if not context.args:
        await update.effective_message.reply_text("Напиши номер плана из /agenda, например: /unplan 12")
        return
    await delete_requested_plan(update.effective_message, _db(context), context.args[0])


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = HELP.format(poll_day=_cfg(context).poll_day)
    if update.effective_chat.type == update.effective_chat.PRIVATE:
        text += "\n<i>Добавьте меня в групповой чат компании — там я и работаю.</i>"
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # track_chat already registered the member; just reply
    await update.effective_message.reply_text(f"Записала, {display_name(update.effective_user)}! 💌")


async def leave_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _db(context).remove_member(update.effective_chat.id, update.effective_user.id)
    await update.effective_message.reply_text("Хорошо, больше не буду напоминать.")


async def poll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db, cfg = _db(context), _cfg(context)
    if any(p["kind"] == "date" for p in db.open_polls(update.effective_chat.id)):
        await update.effective_message.reply_text("Опрос по датам уже идёт — голосуйте там 👆 (или /close)")
        return
    await start_date_poll(context.bot, db, cfg, update.effective_chat.id)


async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    polls = _db(context).open_polls(update.effective_chat.id)
    if not polls:
        await update.effective_message.reply_text("Сейчас нет открытых опросов.")
        return
    await remind(context.bot, _db(context), polls[0], quiet_if_done=False)


async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    polls = _db(context).open_polls(update.effective_chat.id)
    if not polls:
        await update.effective_message.reply_text("Сейчас нет открытых опросов.")
        return
    await finish_poll(context.bot, _db(context), _cfg(context), polls[0])


async def idea_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text(
            "Напишите идею после команды, например:\n/idea ресторан: Сыроварня на Красном Октябре")
        return
    kind, title = planner.parse_idea(text)
    title, _, details = title.partition(" — ")
    _db(context).add_idea(update.effective_chat.id, kind, title.strip(), details.strip(),
                          display_name(update.effective_user))
    await update.effective_message.reply_text(
        f"{planner.KIND_EMOJI[kind]} Сохранила в банк идей! Предложу, когда будем выбирать досуг.")


async def ideas_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ideas = _db(context).unused_ideas(update.effective_chat.id)
    if not ideas:
        await update.effective_message.reply_text(
            "Банк идей пуст — пока буду предлагать стартовые варианты. Добавьте свои через /idea")
        return
    lines = ["<b>Банк идей</b>"]
    for i in ideas[:30]:
        line = f"{planner.KIND_EMOJI.get(i['kind'], '✨')} #{i['id']} {html.escape(i['title'])}"
        if i["details"]:
            line += f" — {html.escape(i['details'])}"
        lines.append(line)
    lines.append("<i>Удалить: /unidea номер</i>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def agenda_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = datetime.now(_cfg(context).tz).date().isoformat()
    meetings = _db(context).upcoming_meetings(update.effective_chat.id, today)
    plans = _db(context).upcoming_plans(update.effective_chat.id, today)
    if not meetings and not plans:
        await update.effective_message.reply_text("В повестке пока пусто. Запустить опрос по датам — /poll")
        return
    lines = ["📒 <b>Общая повестка</b>"]
    for m in meetings:
        line = f"🗓 <b>{html.escape(human_date(m['date']))}</b> — "
        line += html.escape(m["activity"]) if m["activity"] else "<i>выбираем досуг…</i>"
        if m["details"]:
            line += f"\n    📝 {html.escape(m['details'])}"
        lines.append(line)
    if plans:
        lines.append("\n📌 <b>Планы</b>")
        lines.extend(f"#{p['id']} {plan_line(p, links=True)}" for p in plans)
        lines.append("<i>Удалить: /unplan номер</i>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                              link_preview_options=NO_PREVIEW)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = _db(context)
    today = datetime.now(_cfg(context).tz).date().isoformat()
    meetings = db.upcoming_meetings(update.effective_chat.id, today)
    if not meetings:
        await update.effective_message.reply_text("Отменять нечего — встреч в повестке нет.")
        return
    db.cancel_meeting(meetings[0]["id"])
    await update.effective_message.reply_text(
        f"Отменила встречу {human_date(meetings[0]['date'])}. Новый опрос — /poll")


async def poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    answer = update.poll_answer
    db = _db(context)
    poll = db.get_poll(answer.poll_id)
    if poll is None or answer.user is None:
        return
    db.record_vote(answer.poll_id, answer.user.id, answer.option_ids)
    # voters get registered too, so future reminders reach them
    db.upsert_member(poll["chat_id"], answer.user.id, display_name(answer.user))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Error while handling an update", exc_info=context.error)


# --- startup ---

async def set_profile(app: Application) -> None:
    """Keep the bot's name, descriptions and command menu in sync with the code."""
    bot = app.bot
    try:
        # set_my_name is heavily rate-limited, so only call it when the name changed
        if (await bot.get_my_name()).name != BOT_NAME:
            await bot.set_my_name(BOT_NAME)
        await bot.set_my_short_description(SHORT_DESCRIPTION)
        await bot.set_my_description(DESCRIPTION)
        await bot.set_my_commands(COMMANDS)
    except TelegramError as e:
        log.warning("could not update the bot profile: %s", e)


def build_app(cfg: Config) -> Application:
    app = (
        Application.builder()
        .token(cfg.token)
        .defaults(Defaults(tzinfo=cfg.tz))
        .post_init(set_profile)
        .build()
    )
    app.bot_data["db"] = Database(cfg.db_path)
    app.bot_data["cfg"] = cfg

    app.add_handler(TypeHandler(Update, guard), group=-2)
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, track_chat), group=-1)
    app.add_handler(CommandHandler(["start", "help"], start_cmd))
    app.add_handler(CommandHandler("join", join_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("leave", leave_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("poll", poll_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("remind", remind_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("close", close_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("idea", idea_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("ideas", ideas_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("agenda", agenda_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("cancel", cancel_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("unplan", unplan_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("unidea", unidea_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("recs", recs_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("memory", memory_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("forget", forget_cmd, filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("unrec", unrec_cmd, filters.ChatType.GROUPS))
    app.add_handler(PollAnswerHandler(poll_answer))
    if cfg.llm_api_key:
        app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, assistant,
            block=False))  # LLM answers take up to a minute: don't hold up commands and votes
    app.add_error_handler(on_error)

    jq = app.job_queue
    jq.run_monthly(monthly_job, dtime(*cfg.poll_time, tzinfo=cfg.tz), day=cfg.poll_day, name="monthly")
    jq.run_daily(reminder_job, dtime(*cfg.reminder_time, tzinfo=cfg.tz), name="reminders")
    if cfg.morning_time:
        jq.run_daily(morning_job, dtime(*cfg.morning_time, tzinfo=cfg.tz), name="morning")
    if cfg.goodnight_time:
        jq.run_daily(goodnight_job, dtime(*cfg.goodnight_time, tzinfo=cfg.tz), name="goodnight")
    jq.run_repeating(close_due_job, interval=600, first=10, name="close_due")
    jq.run_daily(forget_old_plans_job, dtime(4, 0, tzinfo=cfg.tz), name="forget_old_plans")
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    # httpx logs every request URL, and Telegram URLs contain the bot token
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    cfg = load_config()
    app = build_app(cfg)
    if not cfg.allowed_chats:
        # the ids to copy into ALLOWED_CHATS
        chats = ", ".join(f"{c['chat_id']} ({c['title']})" for c in app.bot_data["db"].chats()) or "none yet"
        log.warning("ALLOWED_CHATS is not set, so anyone can use the bot. Known chats: %s", chats)
    log.info("Gigi started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
