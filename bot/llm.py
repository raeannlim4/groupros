from __future__ import annotations

import logging
import openai
import time
from datetime import datetime, timedelta

from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator

from bot import config
from bot.brief import BriefFacts
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


class MeetingTooLongError(Exception):
    """The transcript was too large to send to the model."""


class MeetingTimeoutError(Exception):
    """The model took too long to summarise the transcript."""


def _is_context_length_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "context length",
            "context_length",
            "maximum context",
            "too many tokens",
            "reduce the length",
            "token limit",
        )
    )


def _extra_body() -> dict:
    """OpenRouter routing hints. Empty when the vars are blank."""
    body: dict = {}
    if config.LLM_PROVIDER_SORT:
        body["provider"] = {"sort": config.LLM_PROVIDER_SORT}
    if config.LLM_REASONING.strip().lower() == "off":
        body["reasoning"] = {"enabled": False}
    return body


async def _chat(model: str, messages: list[dict], max_tokens: int) -> str:
    extra = _extra_body()
    try:
        response = await _client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=max_tokens,
            extra_body=extra or None,
        )
    except Exception:
        if not extra:
            raise
        logger.warning(
            "LLM call rejected extra_body %s; retrying without it.", extra
        )
        response = await _client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=max_tokens,
        )
    return response.choices[0].message.content or ""


async def classify_commitment(
    text: str, sender_name: str, now: datetime
) -> Commitment | None:
    user_prompt = (
        f"Current date and time: {format_for_llm(now)}\n"
        f"Sender's first name: {sender_name}\n"
        f"Message: {text}"
    )
    try:
        raw = await _chat(
            config.CAPTURE_MODEL,
            [
                {"role": "system", "content": CAPTURE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            config.CAPTURE_MAX_TOKENS,
        )
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
        raw = await _chat(
            config.CAPTURE_MODEL,
            [
                {"role": "system", "content": PARSE_TASK_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            config.CAPTURE_MAX_TOKENS,
        )
        parsed = ParsedTask.model_validate_json(_strip_code_fences(raw))
        parsed.due = _sane_due(parsed.due, now)
        return parsed
    except Exception:
        logger.exception("parse_task failed for message: %r", text)
        return None


ASK_SYSTEM_PROMPT = """\
You answer questions about a student project group's past discussion.
Answer ONLY using the provided context.
The context may start with a PROJECT BRIEF section holding authoritative facts \
about the assignment. If a brief fact answers the question, use it and cite it \
as [[brief]].
The context also has a DECISIONS section listing things the group agreed. \
Treat those decisions as authoritative: if a decision answers the question, \
use it and cite its [[<telegram_message_id>]].
Cite the source message(s) behind each claim using [[<telegram_message_id>]].
Keep the answer to at most 3 sentences.
If the answer is not present, reply exactly: I couldn't find that in this \
group's chat or decisions.
"""


async def answer_question(question: str, context: str) -> str:
    return await _chat(
        config.ASK_MODEL,
        [
            {"role": "system", "content": ASK_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Context:\n{context}\n\nQuestion: {question}",
            },
        ],
        config.ASK_MAX_TOKENS,
    )


CATCHUP_SYSTEM_PROMPT = """\
You help one member of a student project group catch up on what they missed.
You get the group's recent decisions and a transcript of recent messages.

Write at most 6 short bullet points, in this order:
1. decisions made
2. files or links shared
3. schedule changes
4. tasks
5. anything that mentions the reader by name or asks for their input; start \
those bullets with "Needs your input: ".
6. off-topic items last.

Every bullet is either project-related or off-topic. Combine ALL off-topic \
items (decisions or discussion unrelated to the group's project) into ONE \
single bullet placed last. Start that bullet with "OFFTOPIC: " and use neutral \
wording only, e.g. "OFFTOPIC: Also logged 3 decisions unrelated to the \
project." Never omit off-topic items. The off-topic bullet must have NO \
citation links.

Each project-related bullet may end with at most 2 message ids, written as \
[[<telegram_message_id>]], copied exactly from the context. Pick the most \
relevant. Use ONLY the provided context. Never invent a message id. If a \
category has nothing, skip it. Keep each bullet to one short line.

Never describe messages or people with judgmental or emotional labels such as \
"romantic", "hostile", "rude" or "weird". Describe what was decided or shared, \
never characterise it.

Return plain text only, one bullet per line, each line starting with "- ".
"""


async def catchup_summary(reader_name: str, context: str) -> str:
    return await _chat(
        config.ASK_MODEL,
        [
            {"role": "system", "content": CATCHUP_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Reader's first name: {reader_name}\n\n"
                    f"Context:\n{context}"
                ),
            },
        ],
        config.ASK_MAX_TOKENS,
    )


MEETING_SYSTEM_PROMPT = """\
You summarise a meeting transcript for a student project group.
The transcript is a cleaned Zoom transcript; each line looks like \
"[mm:ss] Speaker: text" or "[h:mm:ss] Speaker: text", and some lines have no \
speaker name.

Return JSON only, no prose and no code fences, in exactly this shape:
{"title": "Weekly sync", "duration_minutes": 45, "attendees": ["Raeann Lim", "Tan Wei"], "topics": ["survey", "timeline"], "decisions": [{"text": "Use Google Forms for the survey", "timestamp": "03:12", "speaker": "Raeann Lim"}], "action_items": [{"owner_name": "Tan Wei", "task": "draft the survey questions", "due": "2026-10-11T23:59:00+08:00", "timestamp": "05:40"}], "open_questions": ["Who sends the survey to the tutor?"]}

Rules:
- "title": a short title for the meeting.
- "duration_minutes": the whole number of minutes from the first to the last \
timestamp, or null.
- "attendees": the speaker names that appear in the transcript.
- "topics": short phrases for what the group discussed.
- "decisions": things the group agreed. "text" is one short sentence. \
"speaker" is the speaker on that line, or null. "timestamp" MUST be copied \
exactly from a "[...]" in the transcript.
- "action_items": tasks someone agreed to do. "owner_name" is that speaker's \
name, or null if nobody was named. "task" is a short title. "timestamp" MUST be \
copied exactly from a "[...]" in the transcript.
- "due": an ISO 8601 datetime with the local UTC offset, or null. Resolve \
relative dates using the meeting date provided. A day with no time means 23:59 \
that day. A deadline more than 120 days after the meeting date is null.
- "open_questions": questions raised but not answered.
- Every "timestamp" must be copied exactly from the transcript. Never invent one.
"""


class MeetingDecision(BaseModel):
    text: str = ""
    timestamp: str = ""
    speaker: str | None = None

    @field_validator("text", "timestamp", mode="before")
    @classmethod
    def _none_to_empty(cls, value):
        return value or ""

    @field_validator("speaker", mode="before")
    @classmethod
    def _none_speaker(cls, value):
        return value or None


class MeetingActionItem(BaseModel):
    owner_name: str | None = None
    task: str = ""
    due: datetime | None = None
    timestamp: str = ""

    @field_validator("task", "timestamp", mode="before")
    @classmethod
    def _none_to_empty(cls, value):
        return value or ""

    @field_validator("owner_name", "due", mode="before")
    @classmethod
    def _empty_to_none(cls, value):
        return value or None


class MeetingSummary(BaseModel):
    title: str = ""
    duration_minutes: int | None = None
    attendees: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    decisions: list[MeetingDecision] = Field(default_factory=list)
    action_items: list[MeetingActionItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @field_validator("title", mode="before")
    @classmethod
    def _title_none_to_empty(cls, value):
        return value or ""

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def _duration_none(cls, value):
        return value or None

    @field_validator(
        "attendees",
        "topics",
        "decisions",
        "action_items",
        "open_questions",
        mode="before",
    )
    @classmethod
    def _list_none_to_empty(cls, value):
        return value or []


async def summarize_meeting(
    transcript: str, meeting_date: datetime
) -> MeetingSummary:
    user_prompt = (
        f"Meeting date and time: {format_for_llm(meeting_date)}\n\n"
        f"Transcript:\n{transcript}"
    )
    try:
        raw = await _chat(
            config.ASK_MODEL,
            [
                {"role": "system", "content": MEETING_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            config.ASK_MAX_TOKENS,
        )
    except openai.APITimeoutError as exc:
        raise MeetingTimeoutError() from exc
    except openai.BadRequestError as exc:
        if _is_context_length_error(exc):
            raise MeetingTooLongError() from exc
        raise
    summary = MeetingSummary.model_validate_json(_strip_code_fences(raw))
    for item in summary.action_items:
        item.due = _sane_due(item.due, meeting_date)
    return summary


class BriefTooLongError(Exception):
    """The assignment brief was too large to send to the model."""


class BriefTimeoutError(Exception):
    """The model took too long to read the assignment brief."""


BRIEF_SYSTEM_PROMPT = """\
You read an assignment brief for a student project group and extract its key facts.

Return JSON only, no prose and no code fences, in exactly this shape:
{"final_deadline": {"value": "Friday 11 Oct 2026, 11:59pm", "date": "2026-10-11T23:59:00+08:00", "quote": "Final submission due Friday 11 October 2026 by 23:59."}, "presentation": {"value": "Week 12, in class, 10 minutes", "date": null, "quote": "Presentation in Week 12 (10 minutes)."}, "deliverables": [{"value": "Report — PDF, max 3000 words", "date": null, "quote": "Submit a report (PDF) of no more than 3000 words."}], "word_limit": {"value": "3000 words", "date": null, "quote": "no more than 3000 words"}, "group_size": {"value": "4 to 5 students", "date": null, "quote": "Groups of 4 to 5 students."}, "weighting": {"value": "40% of the final grade", "date": null, "quote": "This assignment is worth 40% of your final grade."}, "other_key_dates": [{"value": "Proposal due Week 8", "date": null, "quote": "Proposal due Week 8."}]}

Rules:
- Every fact has "value" (what it says, in your words), "date" (an ISO 8601 datetime with the local UTC offset, or null) and "quote" (the exact source sentence copied from the document).
- "final_deadline" is the assignment's final submission deadline.
- "presentation" is when and how long the presentation is; "date" is the presentation date when the document states one.
- "deliverables" is what must be submitted, each with its format and any limits.
- "word_limit", "group_size" and "weighting" are as stated.
- "other_key_dates" holds the other important dates.
- "date" MUST be null when the document gives a relative or teaching-week date (such as "Week 11") instead of a calendar date. Never guess a date.
- "quote" MUST be copied exactly from the document so it can be found there. If a fact is not in the document, use null (or an empty list).
"""


async def extract_brief(text: str, document_date: datetime) -> BriefFacts:
    user_prompt = (
        f"Document date and time: {format_for_llm(document_date)}\n\n"
        f"Document:\n{text}"
    )
    try:
        raw = await _chat(
            config.ASK_MODEL,
            [
                {"role": "system", "content": BRIEF_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            config.ASK_MAX_TOKENS,
        )
    except openai.APITimeoutError as exc:
        raise BriefTimeoutError() from exc
    except openai.BadRequestError as exc:
        if _is_context_length_error(exc):
            raise BriefTooLongError() from exc
        raise
    return BriefFacts.model_validate_json(_strip_code_fences(raw))


PLAN_SYSTEM_PROMPT = """\
You plan milestones for a student project group working towards a deadline.
You get the final deadline, the assignment brief's facts (if any) and the group's existing open tasks.

Return JSON only, no prose and no code fences, in exactly this shape:
{"milestones": [{"title": "Draft survey questions", "due": "2026-10-05T23:59:00+08:00"}]}

Rules:
- Generate 4 to 7 milestones that work backwards from the deadline, leaving at least one buffer day before the deadline.
- "title" is a short, actionable milestone title.
- "due" is an ISO 8601 datetime with the local UTC offset. A day with no time means 23:59 that day.
- Every milestone's date MUST be on or after today and on or before the deadline. Resolve relative dates using the current date provided.
- Do NOT duplicate the existing open tasks; create new, distinct milestones.
- Order the milestones from earliest to latest.
"""


class PlanMilestone(BaseModel):
    title: str = ""
    due: datetime | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _title_none_to_empty(cls, value):
        return value or ""

    @field_validator("due", mode="before")
    @classmethod
    def _empty_due_to_none(cls, value):
        return value or None


class PlanList(BaseModel):
    milestones: list[PlanMilestone] = Field(default_factory=list)

    @field_validator("milestones", mode="before")
    @classmethod
    def _none_to_empty(cls, value):
        return value or []


async def plan_milestones(
    deadline: datetime,
    brief_text: str,
    existing_titles: list[str],
    now: datetime,
) -> list[PlanMilestone]:
    if existing_titles:
        existing = "\n".join(f"- {title}" for title in existing_titles)
    else:
        existing = "- (none)"
    user_prompt = (
        f"Current date and time: {format_for_llm(now)}\n"
        f"Final deadline: {format_for_llm(deadline)}\n\n"
        f"Existing open tasks:\n{existing}\n\n"
        f"Assignment brief:\n{brief_text or '(no brief uploaded)'}"
    )
    raw = await _chat(
        config.ASK_MODEL,
        [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        config.ASK_MAX_TOKENS,
    )
    return PlanList.model_validate_json(_strip_code_fences(raw)).milestones
