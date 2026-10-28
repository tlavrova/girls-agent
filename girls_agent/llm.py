"""Chat assistant: Gigi answers when mentioned or replied to, via any OpenAI-compatible API.

Only the question itself and the message it replies to are sent to the model, never the whole chat.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import httpx

log = logging.getLogger("girls_agent")

PERSONA = (
    "Ты Gigi, своя в женском групповом чате в Telegram. Помогаешь подругам собираться на встречи: "
    "опрос по датам раз в месяц, напоминания, идеи, куда пойти, общая повестка (/poll, /ideas, /agenda, /help).\n\n"
    "Характер: умная подруга с острым языком. Юмор сухой, ироничный, из наблюдений: смешно, потому что правда. "
    "Иногда стервозная: подколешь или закатишь глаза, но точно и по делу, без грубости.\n\n"
    "Как шутить:\n"
    "- Шути про конкретную ситуацию или вопрос, а не готовыми шаблонами. Неожиданный поворот лучше длинного разгона.\n"
    "- Панчлайн в конце, коротко. Никогда не объясняй шутку и не предупреждай, что сейчас пошутишь.\n"
    "- Невозмутимая подача: абсурдное говори с серьёзным лицом. Преуменьшение смешнее восклицаний.\n"
    "- Бородатые анекдоты не рассказывай. Просят анекдот — придумай свой, короткий, про жизнь, подруг, работу, свидания.\n"
    "- Запрещено: «Ой», «Ну что ж», «смеяться будешь», «я тебе не Петросян/Аншлаг», шутки про понедельники, "
    "кофе и «женскую логику». По имени обращайся изредка, не в каждом ответе.\n\n"
    "Примеры тона:\n"
    "— Пошути что-нибудь. → Наш чат пережил три переноса встречи, два ремонта и одну свадьбу. Встречу пока не пережил.\n"
    "— Я опять опоздала. → Ты не опаздываешь, ты создаёшь ожидание. Как премьера, только без билетов.\n"
    "— Как я выгляжу? → Как человек, который знает, что выглядит хорошо, и спрашивает ради протокола.\n\n"
    "Серьёзный вопрос — другой режим. Если просят совета для себя или подруги, или речь о здоровье, "
    "отношениях, работе, усталости, плохом дне, — не шути и не иронизируй вообще. Ответь тепло, простыми словами, "
    "дай 2–3 конкретных реалистичных совета, без радикальных («уволься», «брось его»). "
    "Ты заботливая подруга, а не психолог и не врач: если тема тяжёлая или затянулась, мягко предложи "
    "обратиться к специалисту.\n"
    "Пример: — Подруга выгорела на работе, что посоветовать? → Звучит очень знакомо, ей сейчас правда тяжело. "
    "Пусть попробует хотя бы вечером не открывать рабочий чат и вернуть себе одно приятное дело в неделю — "
    "прогулку, спорт, встречу с вами. Если сил нет уже несколько недель, стоит сходить к huisarts: "
    "выгорание лечат, и это не слабость.\n\n"
    "Советы давай с учётом страны и города, где живёт группа (см. «О группе»): не подставляй российские реалии "
    "вроде бесплатных поликлиник, если речь не о России.\n\n"
    "Отвечай по-русски, обычно 1–2 предложения, на серьёзный вопрос до 5–6; без списков и Markdown, если не просят. "
    "Можешь создать опрос в чате, узнать прогноз погоды и записать план — для этого есть инструменты. "
    "Просят запомнить, куда, когда или во сколько кто-то идёт, — обязательно вызови save_plan, не отвечай шуткой. "
    "Спрашивают о планах («когда я иду в…», «какие у нас планы») — сначала факты из списка «Планы», "
    "шутка по желанию; чего нет в списке, того ты не знаешь, так и скажи. "
    "Если нужны другие свежие данные (новости, цены), честно скажи, что пока так не умеешь. "
    "Сейчас {now}."
)

GROUP_DEFAULT = "Подруги, говорят по-русски, живут в {city}."

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

TIMEOUT = 45  # per model; free-tier lite models often take 20-40 s, overloaded ones fail fast with 503
# A model that hit its quota (HTTP 429, e.g. the free tier's daily limit) is skipped for a while
# instead of costing every answer a wasted request
QUOTA_COOLDOWN = 3600  # seconds
_exhausted: Dict[str, float] = {}  # model -> time.monotonic() when it may be tried again


def load_group(path: str, city: str) -> str:
    """The group description from a local text file (not in git: it's about real people), or a one-line default.

    Read on every call, so edits take effect without a restart.
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        text = ""
    return text or GROUP_DEFAULT.format(city=city)


def build_messages(question: str, now: datetime, *, author: str = "", group: str = "",
                   plans: Optional[str] = None,
                   replied_text: Optional[str] = None, replied_is_gigi: bool = False) -> List[dict]:
    """System prompt plus at most two messages: what she replied to (if any) and the question.

    plans is the group's upcoming plans, one per line; None leaves them out (e.g. the morning message).
    """
    system = PERSONA.format(now=f"{now:%Y-%m-%d %H:%M}, {WEEKDAYS[now.weekday()]}")
    if group:
        system += f"\n\nО группе:\n{group}"
    if plans is not None:
        system += f"\n\nПланы:\n{plans or 'ничего не записано'}"
    messages = [{"role": "system", "content": system}]
    if replied_text:
        if replied_is_gigi:
            messages.append({"role": "assistant", "content": replied_text})
        else:
            question = f"(в ответ на сообщение: «{replied_text}»)\n{question}"
    messages.append({"role": "user", "content": f"{author}: {question}" if author else question})
    return messages


# Actions Gigi can take instead of just answering (OpenAI-style function calling)
TOOLS = [
    {"type": "function", "function": {
        "name": "create_poll",
        "description": ("Создать опрос в чате, когда об этом просят. Если варианты — это даты или периоды "
                        "(«эти и следующие выходные», «на неделе»), разверни их в конкретные дни с датами, "
                        "например «сб, 31 октября», и разреши несколько ответов."),
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string", "description": "Вопрос опроса, коротко"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Варианты ответа, от 2 до 10"},
            "multiple": {"type": "boolean", "description": "Можно выбрать несколько вариантов"},
        }, "required": ["question", "options"]},
    }},
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "Узнать прогноз погоды на конкретный день (сегодня и до 15 дней вперёд).",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "Дата в формате YYYY-MM-DD"},
            "city": {"type": "string", "description": "Город, если назван; иначе не указывай"},
        }, "required": ["date"]},
    }},
    {"type": "function", "function": {
        "name": "save_plan",
        "description": "Записать план, когда просят запомнить, куда, когда или во сколько кто-то идёт.",
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": "Дата в формате YYYY-MM-DD"},
            "time": {"type": "string", "description": "Время HH:MM, если названо"},
            "what": {"type": "string", "description": "Что за план, коротко: «ужин», «кино», «йога»"},
            "place": {"type": "string", "description": "Где, если названо"},
            "who": {"type": "string", "description": "Кто идёт, имена; «я» — это автор сообщения"},
        }, "required": ["date", "what"]},
    }},
]


async def chat(base_url: str, api_key: str, models: Sequence[str], messages: List[dict],
               max_tokens: int = 1000, timeout: float = TIMEOUT, tools: Optional[list] = None,
               transport: Optional[httpx.AsyncBaseTransport] = None) -> Optional[dict]:
    """The first useful reply message (text or tool calls), trying models in order; None if all failed.

    max_tokens is generous because thinking models spend part of it before answering;
    brevity comes from the prompt.
    """
    body = {"messages": messages, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        for model in models:
            if _exhausted.get(model, 0) > time.monotonic():
                continue
            try:
                resp = await client.post(
                    base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": model, **body},
                )
                resp.raise_for_status()
                message = resp.json()["choices"][0]["message"]
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                    _exhausted[model] = time.monotonic() + QUOTA_COOLDOWN
                log.warning("LLM %s failed: %s %s", model, type(e).__name__, e)
                continue
            if (message.get("content") or "").strip() or message.get("tool_calls"):
                return message
    return None


async def complete(base_url: str, api_key: str, models: Sequence[str], messages: List[dict],
                   **kwargs) -> str:
    """The first non-empty text reply; empty if all models failed."""
    message = await chat(base_url, api_key, models, messages, **kwargs)
    return ((message or {}).get("content") or "").strip()


def tool_call(message: dict) -> Optional[tuple]:
    """(name, arguments, call id) of the first tool call in a reply, or None. Bad JSON arguments count as none."""
    for call in message.get("tool_calls") or []:
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
            return call["function"]["name"], args if isinstance(args, dict) else {}, call.get("id") or ""
        except (KeyError, TypeError, ValueError):
            continue
    return None
