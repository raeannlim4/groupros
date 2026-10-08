from __future__ import annotations

import logging
from datetime import datetime, timedelta

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

from bot import config
from bot.timeutil import format_for_llm

logger = logging.getLogger(__name__)

MAX_DUE_DAYS = 120

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


def _sane_due(due: datetime | None, now: datetime) -> datetime | None:
    if due is not None and due > now + timedelta(days=MAX_DUE_DAYS):
        return None
    return due


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
        commitment = Commitment.model_validate_json(_strip_code_fences(raw))
        commitment.due = _sane_due(commitment.due, now)
        return commitment
    except Exception:
        logger.exception("classify_commitment failed for message: %r", text)
        return None


PARSE_TASK_SYSTEM_PROMPT = """\
You extract a task title and deadline from a message a student typed in a \
Singaporean project group chat. The user is deliberately creating a task, so \
do NOT judge whether the message is a commitment.

Return JSON only, no prose and no code fences, in exactly this shape:
{"task": "slides", "due": "2026-10-09T23:59:00+08:00"}

Rules:
- "task" is a short title in the user's words, with the deadline words \
removed. e.g. "slides by tmr" -> "slides"; "refs by thurs 9pm" -> "refs".
- "due" is an ISO 8601 datetime with the local UTC offset, or null if there \
is no deadline.
- Resolve relative dates using the current date and time provided.
- A day with no time means 23:59 that day.
- Shorthand: tmr/tmrw/tml = tomorrow; tdy = today; eod = today 23:59; \
nxt wk = 7 days from today at 23:59.
- Weekdays (mon/tues/wed/thurs/fri/sat/sun): a bare weekday or "this \
<weekday>" means the next upcoming such weekday (today counts if it is that \
weekday). "next <weekday>" means that weekday in the following week (the \
calendar week after the current one), e.g. if today is Thursday then "next \
tues" is the Tuesday of the following week.
- "by 9pm" means 21:00 on the resolved day.
"""


class ParsedTask(BaseModel):
    task: str = ""
    due: datetime | None = None

    @field_validator("task", mode="before")
    @classmethod
    def _task_none_to_empty(cls, value):
        return value or ""

    @field_validator("due", mode="before")
    @classmethod
    def _empty_due_to_none(cls, value):
        return value or None


async def parse_task(text: str, now: datetime) -> ParsedTask | None:
    user_prompt = (
        f"Current date and time: {format_for_llm(now)}\n"
        f"Message: {text}"
    )
    try:
        response = await _client.chat.completions.create(
            model=config.CAPTURE_MODEL,
            messages=[
                {"role": "system", "content": PARSE_TASK_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        raw = response.choices[0].message.content or ""
        parsed = ParsedTask.model_validate_json(_strip_code_fences(raw))
        parsed.due = _sane_due(parsed.due, now)
        return parsed
    except Exception:
        logger.exception("parse_task failed for message: %r", text)
        return None
