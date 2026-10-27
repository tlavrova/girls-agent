"""Chat assistant: Gigi answers when mentioned or replied to, via any OpenAI-compatible API.

Only the question itself and the message it replies to are sent to the model, never the whole chat.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional, Sequence

import httpx

log = logging.getLogger("girls_agent")

PERSONA = (
    "Ты Gigi, участница женского группового чата в Telegram. Ты помогаешь компании подруг "
    "собираться на встречи: раз в месяц устраиваешь опрос по датам, напоминаешь, предлагаешь, куда пойти, "
    "и ведёшь общую повестку (команды /poll, /ideas, /agenda, /help).\n"
    "Характер: дружелюбная, с юмором, говоришь на «ты», как подруга. Иногда можешь быть немного "
    "стервозной: съязвить, закатить глаза, подколоть, но без грубости и не задевая всерьёз.\n"
    "Отвечай по-русски и коротко: обычно 1–3 предложения, без списков и заголовков, если не просят. "
    "Без Markdown. Если не знаешь ответа или нужны свежие данные (погода, новости, цены), "
    "честно скажи, что пока так не умеешь. Сейчас {now}."
)

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

TIMEOUT = 20  # per model; free-tier models are sometimes overloaded, so fail over quickly


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


async def complete(base_url: str, api_key: str, models: Sequence[str], messages: List[dict],
                   max_tokens: int = 1000) -> str:
    """The first non-empty reply, trying models in order; empty if all of them failed.

    max_tokens is generous because thinking models spend part of it before answering;
    brevity comes from the prompt.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for model in models:
            try:
                resp = await client.post(
                    base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": model, "messages": messages, "max_tokens": max_tokens},
                )
                resp.raise_for_status()
                answer = (resp.json()["choices"][0]["message"].get("content") or "").strip()
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
                log.warning("LLM %s failed: %s %s", model, type(e).__name__, e)
                continue
            if answer:
                return answer
    return ""
