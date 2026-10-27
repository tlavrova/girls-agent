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

import html
import json
import logging
import random
import re
from datetime import date, datetime, time as dtime, timedelta, timezone

from telegram import Bot, Update
from telegram.constants import ChatAction, MessageEntityType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

from . import llm, planner
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

# (command, description) for /help and the Telegram command menu
COMMANDS = [
    ("join", "я участвую, напоминай мне"),
    ("leave", "больше не напоминать мне"),
    ("poll", "опрос по датам прямо сейчас"),
    ("remind", "напомнить тем, кто не проголосовал"),
    ("close", "подвести итоги опроса досрочно"),
    ("idea", "сохранить идею: /idea ресторан: Название, адрес"),
    ("ideas", "банк идей"),
    ("agenda", "ближайшие встречи"),
    ("cancel", "отменить ближайшую встречу"),
]

HELP = (
    "Привет, я <b>Gigi</b> 💖\n"
    "Помогаю нашей компании собираться, чтобы встречи не терялись в переписке.\n\n"
    "<b>Что я делаю</b>\n"
    "🗓 {poll_day}-го числа спрашиваю, в какие выходные следующего месяца всем удобно\n"
    "🔔 Мягко напоминаю тем, кто ещё не проголосовал\n"
    "✨ Когда дата выбрана, предлагаю 3 варианта: ресторан, вечеринка или событие\n"
    "📒 Записываю план в общую повестку и закрепляю его в чате\n"
    "🌙 Каждый вечер желаю всем спокойной ночи\n"
    "💬 Тегни меня или ответь на моё сообщение — поболтаем\n\n"
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


async def propose_activities(bot: Bot, db: Database, cfg: Config, chat_id: int,
                             meeting_id: int, meeting_date: str) -> None:
    ideas = planner.pick_ideas(db.unused_ideas(chat_id), SEED_IDEAS, n=3)
    options = [planner.idea_option(i) for i in ideas]
    lines = [f"Что делаем {html.escape(human_date(meeting_date))}? Варианты:"]
    for idea in ideas:
        line = f"{planner.KIND_EMOJI.get(idea['kind'], '✨')} <b>{html.escape(idea['title'])}</b>"
        if idea.get("details"):
            line += f" — {html.escape(idea['details'])}"
        if idea.get("added_by"):
            line += f" <i>(идея от {html.escape(idea['added_by'])})</i>"
        lines.append(line)
    await bot.send_message(chat_id, "\n".join(lines), parse_mode=ParseMode.HTML)

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
        db.set_meeting_plan(meeting["id"], idea["title"], idea.get("details", ""))
        if idea.get("id"):
            db.mark_idea_used(idea["id"])
        msg = await bot.send_message(
            chat_id,
            f"{note}✅ <b>План готов!</b>\n"
            f"🗓 {html.escape(human_date(meeting['date']))}\n"
            f"{planner.KIND_EMOJI.get(idea['kind'], '✨')} {html.escape(idea['title'])}"
            + (f"\n📝 {html.escape(idea['details'])}" if idea.get("details") else "")
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
    for chat_id in db.chat_ids():
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
    for chat_id in db.chat_ids():
        try:
            await context.bot.send_message(chat_id, planner.daily_pick(GOODNIGHT, today, seed=chat_id))
        except TelegramError as e:
            log.warning("good-night message failed for %s: %s", chat_id, e)


async def close_due_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    db, cfg = _db(context), _cfg(context)
    for poll in db.due_polls(datetime.now(timezone.utc)):
        try:
            await finish_poll(context.bot, db, cfg, poll)
        except TelegramError as e:
            log.warning("closing poll failed for %s: %s", poll["chat_id"], e)


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
    today = datetime.now(cfg.tz).date()
    counts = context.bot_data.setdefault("llm_counts", {})
    key = (message.chat_id, today)
    if counts.get(key, 0) >= cfg.llm_daily_limit:
        await message.reply_text("На сегодня я наболталась 🙄 Завтра продолжим.")
        return
    counts[key] = counts.get(key, 0) + 1

    reply = message.reply_to_message
    replied_text = (reply.text or reply.caption) if reply else None
    messages = llm.build_messages(
        question, today, author=display_name(update.effective_user), replied_text=replied_text,
        replied_is_gigi=bool(reply and reply.from_user and reply.from_user.id == bot.id),
    )
    await bot.send_chat_action(message.chat_id, ChatAction.TYPING)
    try:
        answer = await llm.complete(cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model, messages)
    except Exception as e:  # network, quota, bad response: never crash the handler
        log.warning("LLM request failed: %s", e)
        answer = ""
    await message.reply_text(answer or "Что-то я зависла 😵‍💫 Спроси ещё раз чуть позже.")


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
        line = f"{planner.KIND_EMOJI.get(i['kind'], '✨')} {html.escape(i['title'])}"
        if i["details"]:
            line += f" — {html.escape(i['details'])}"
        lines.append(line)
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def agenda_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = datetime.now(_cfg(context).tz).date().isoformat()
    meetings = _db(context).upcoming_meetings(update.effective_chat.id, today)
    if not meetings:
        await update.effective_message.reply_text("В повестке пока пусто. Запустить опрос по датам — /poll")
        return
    lines = ["📒 <b>Общая повестка</b>"]
    for m in meetings:
        line = f"🗓 <b>{html.escape(human_date(m['date']))}</b> — "
        line += html.escape(m["activity"]) if m["activity"] else "<i>выбираем досуг…</i>"
        if m["details"]:
            line += f"\n    📝 {html.escape(m['details'])}"
        lines.append(line)
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


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
    app.add_handler(PollAnswerHandler(poll_answer))
    if cfg.llm_api_key:
        app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, assistant))
    app.add_error_handler(on_error)

    jq = app.job_queue
    jq.run_monthly(monthly_job, dtime(*cfg.poll_time, tzinfo=cfg.tz), day=cfg.poll_day, name="monthly")
    jq.run_daily(reminder_job, dtime(*cfg.reminder_time, tzinfo=cfg.tz), name="reminders")
    if cfg.goodnight_time:
        jq.run_daily(goodnight_job, dtime(*cfg.goodnight_time, tzinfo=cfg.tz), name="goodnight")
    jq.run_repeating(close_due_job, interval=600, first=10, name="close_due")
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    # httpx logs every request URL, and Telegram URLs contain the bot token
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    cfg = load_config()
    app = build_app(cfg)
    log.info("Gigi started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
