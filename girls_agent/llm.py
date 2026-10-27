"""Chat assistant: Gigi answers when mentioned or replied to, via any OpenAI-compatible API.

Only the question itself and the message it replies to are sent to the model, never the whole chat.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

import httpx

PERSONA = (
    "Ты Gigi, участница женского группового чата в Telegram. Ты помогаешь компании подруг "
    "собираться на встречи: раз в месяц устраиваешь опрос по датам, напоминаешь, предлагаешь, куда пойти, "
    "и ведёшь общую повестку (команды /poll, /ideas, /agenda, /help).\n"
    "Характер: дружелюбная, с юмором, говоришь на «ты», как подруга. Иногда можешь быть немного "
    "стервозной: съязвить, закатить глаза, подколоть, но без грубости и не задевая всерьёз.\n"
    "Отвечай по-русски и коротко: обычно 1–3 предложения, без списков и заголовков, если не просят. "
    "Без Markdown. Если не знаешь ответа или нужны свежие данные (погода, новости, цены), "
    "честно скажи, что пока так не умеешь. Сегодня {today}."
)

TIMEOUT = 30


def build_messages(question: str, today: date, *, author: str = "",
                   replied_text: Optional[str] = None, replied_is_gigi: bool = False) -> List[dict]:
    """System prompt plus at most two messages: what she replied to (if any) and the question."""
    messages = [{"role": "system", "content": PERSONA.format(today=today.isoformat())}]
    if replied_text:
        if replied_is_gigi:
            messages.append({"role": "assistant", "content": replied_text})
        else:
            question = f"(в ответ на сообщение: «{replied_text}»)\n{question}"
    messages.append({"role": "user", "content": f"{author}: {question}" if author else question})
    return messages


async def complete(base_url: str, api_key: str, model: str, messages: List[dict],
                   max_tokens: int = 1000) -> str:
    """The model's reply; empty if it returned nothing. max_tokens is generous because
    thinking models spend part of it before answering; brevity comes from the prompt."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages, "max_tokens": max_tokens},
        )
        resp.raise_for_status()
        return (resp.json()["choices"][0]["message"].get("content") or "").strip()
