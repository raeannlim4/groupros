from __future__ import annotations

import logging
from datetime import datetime

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

from bot import config
from bot.timeutil import format_for_llm

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    base_url=config.LLM_BASE_URL,
    api_key=config.LLM_API_KEY,
    timeout=30.0,
)

CAPTURE_SYSTEM_PROMPT = """\
You analyse messages from a project group chat between Singaporean students. \
The chat may contain Singlish (e.g. "can", "lah", "settle", "already").

Decide whether the SENDER is committing to do a task themselves. Only \
self-commitments count, e.g. "I'll do X", "I settle X", "can, I take X", \
"leave it to me". Requests to others, questions, jokes and past-tense reports \
do NOT count.

Not every "I can" is a commitment. Statements of ability, availability or \
plans outside the project (e.g. "i can try to come tomorrow", "I can go home \
now") are NOT commitments. Count only when the sender is personally taking on \
a concrete piece of project work. When unsure, prefer is_commitment false so \
the bot does not interrupt the discussion.

Deadlines:
- Use the current date and time provided to resolve relative deadlines.
- A day with no time means 23:59 that day.
- "By Thursday" means the next upcoming Thursday.
- If there is no deadline, use null.

Return JSON only, no prose and no code fences, in exactly this shape:
{"is_commitment": true, "task": "competitor research", "due": "2026-10-09T23:59:00+08:00", "confidence": 0.9}

- "task" is a short description of the task, in the sender's words.
- "due" is an ISO 8601 datetime with the local UTC offset, or null.
- "confidence" is a number from 0 to 1.
"""


class Commitment(BaseModel):
    is_commitment: bool
    task: str = ""
    due: datetime | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("task", mode="before")
    @classmethod
    def _task_none_to_empty(cls, value):
        return value or ""

    @field_validator("due", mode="before")
    @classmethod
    def _empty_due_to_none(cls, value):
        return value or None

    @field_validator("confidence", mode="before")
    @classmethod
    def _none_confidence_to_zero(cls, value):
        return 0.0 if value is None else value


def _strip_code_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        newline = text.find("\n")
        if newline != -1:
            text = text[newline + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


async def classify_commitment(
    text: str, sender_name: str, now: datetime
) -> Commitment | None:
    user_prompt = (
        f"Current date and time: {format_for_llm(now)}\n"
        f"Sender's first name: {sender_name}\n"
        f"Message: {text}"
    )
    try:
        response = await _client.chat.completions.create(
            model=config.CAPTURE_MODEL,
            messages=[
                {"role": "system", "content": CAPTURE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        raw = response.choices[0].message.content or ""
        return Commitment.model_validate_json(_strip_code_fences(raw))
    except Exception:
        logger.exception("classify_commitment failed for message: %r", text)
        return None
