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
    "Отвечай по-русски, 1–2 предложения, без списков и Markdown, если не просят. "
    "Можешь создать опрос в чате и узнать прогноз погоды — для этого есть инструменты. "
    "Если нужны другие свежие данные (новости, цены), честно скажи, что пока так не умеешь. "
    "Сейчас {now}."
)

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

TIMEOUT = 45  # per model; free-tier lite models often take 20-40 s, overloaded ones fail fast with 503
# A model that hit its quota (HTTP 429, e.g. the free tier's daily limit) is skipped for a while
# instead of costing every answer a wasted request
QUOTA_COOLDOWN = 3600  # seconds
_exhausted: Dict[str, float] = {}  # model -> time.monotonic() when it may be tried again


def build_messages(question: str, now: datetime, *, author: str = "",
                   replied_text: Optional[str] = None, replied_is_gigi: bool = False) -> List[dict]:
    """System prompt plus at most two messages: what she replied to (if any) and the question."""
    messages = [{"role": "system", "content": PERSONA.format(now=f"{now:%Y-%m-%d %H:%M}, {WEEKDAYS[now.weekday()]}")}]
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
        "description": "Создать опрос в чате, когда об этом просят.",
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
