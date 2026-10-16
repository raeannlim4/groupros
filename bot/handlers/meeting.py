"""``/meeting`` — summarise a meeting transcript into minutes with buttons."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from bot.db import (
    SessionLocal,
    get_or_create_chat,
    get_or_create_chat_member,
    get_or_create_user,
)
from bot.grounding import normalise_for_grounding
from bot.handlers.reminders import create_reminders
from bot.llm import (
    MeetingActionItem,
    MeetingDecision,
    MeetingSummary,
    MeetingTimeoutError,
    MeetingTooLongError,
    summarize_meeting,
)
from bot.models import ChatMember, Decision, Message, Task, User
from bot.timeutil import format_absolute, now_utc, to_local
from bot.vtt import MAX_TRANSCRIPT_CHARS, MAX_TRANSCRIPT_WORDS, clean_vtt

logger = logging.getLogger(__name__)

USAGE = (
    "Send me a meeting transcript and I'll turn it into minutes.\n\n"
    "Host: download the transcript (.vtt) from Zoom → Recordings & Transcripts, "
    "then send it here with /meeting. You can attach it as a document with the "
    "caption /meeting, or reply /meeting to a document or a text message."
)
PRIVATE = "Use /meeting in a group chat."
FAILED = "Sorry, something went wrong, try again."
NOTHING = "I couldn't find clear decisions or action items in this transcript."
EMPTY_FILE = (
    "That transcript looks empty. Check you downloaded the Audio transcript "
    "(.vtt) from Zoom."
)
UNSUPPORTED = "I can only read .vtt (Zoom transcript), .txt files, or pasted text."
DECODE_ERROR = (
    "I couldn't read that file. Make sure it's the .vtt transcript from Zoom, "
    "not the video or audio recording."
)
TIMEOUT = (
    "Summarising took too long. Try again, or split the transcript into "
    "smaller parts."
)
MAX_FILE_BYTES = 1_000_000

_TIMESTAMP = re.compile(r"\[(\d{1,3}:\d{2}(?::\d{2})?)\]")


class TranscriptError(Exception):
    """A problem with the transcript that has a specific user-facing reply."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _word_count(text: str) -> int:
    return len(text.split())


def _too_long_message(words: int) -> str:
    return (
        f"This transcript is too long for me to summarise (about {words} words; "
        f"my limit is about {MAX_TRANSCRIPT_WORDS} words, roughly a 90-minute "
        "meeting). Try splitting it into parts and sending each one with /meeting."
    )


@dataclass
class _Pending:
    chat_db_id: int
    decisions: list[MeetingDecision]
    actions: list[MeetingActionItem]
    owners: list[tuple[int | None, str | None]]
    logged: set[int] = field(default_factory=set)
    added: set[int] = field(default_factory=set)


_pending: dict[tuple[int, int], _Pending] = {}


def _to_seconds(value: str) -> int | None:
    try:
        parts = value.strip().split(":")
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + int(seconds)
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    except (TypeError, ValueError):
        return None
    return None


def _timestamp_labels(transcript: str) -> dict[int, str]:
    labels: dict[int, str] = {}
    for label in _TIMESTAMP.findall(transcript):
        seconds = _to_seconds(label)
        if seconds is not None and seconds not in labels:
            labels[seconds] = label
    return labels


def _match_timestamp(value: str, labels: dict[int, str]) -> str | None:
    if not value:
        return None
    seconds = _to_seconds(value)
    if seconds is not None and seconds in labels:
        return labels[seconds]
    needle = normalise_for_grounding(value)
    if needle:
        for label in labels.values():
            if normalise_for_grounding(label) == needle:
                return label
    return None


def _ground(summary: MeetingSummary, transcript: str) -> None:
    labels = _timestamp_labels(transcript)
    extracted = len(summary.decisions) + len(summary.action_items)
    decisions = []
    for decision in summary.decisions:
        label = _match_timestamp(decision.timestamp, labels)
        if label is not None:
            decision.timestamp = label
            decisions.append(decision)
        else:
            logger.debug(
                "Dropped meeting decision, timestamp not in transcript: %r (%r)",
                decision.timestamp,
                decision.text,
            )
    summary.decisions = decisions
    actions = []
    for action in summary.action_items:
        label = _match_timestamp(action.timestamp, labels)
        if label is not None:
            action.timestamp = label
            actions.append(action)
        else:
            logger.debug(
                "Dropped meeting action item, timestamp not in transcript: %r (%r)",
                action.timestamp,
                action.task,
            )
    summary.action_items = actions
    logger.info(
        "Meeting grounding: kept %d of %d items",
        len(summary.decisions) + len(summary.action_items),
        extracted,
    )


def _date_label(moment) -> str:
    local = to_local(moment)
    label = f"{local.strftime('%a')} {local.day} {local.strftime('%b')}"
    if local.year != to_local(now_utc()).year:
        label += f" {local.year}"
    return label


def _short(text: str, limit: int = 28) -> str:
    text = " ".join(text.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _render(summary: MeetingSummary, meeting_date) -> str:
    lines = [f"📋 {summary.title.strip() or 'Meeting'} — {_date_label(meeting_date)}"]
    if summary.duration_minutes:
        lines[0] += f", {summary.duration_minutes} min"
    attendees = [name.strip() for name in summary.attendees if name.strip()]
    if attendees:
        lines.append("👥 " + ", ".join(attendees))
    topics = [topic.strip() for topic in summary.topics if topic.strip()]
    if topics:
        lines.append("")
        lines.append("Discussed:")
        lines.extend(f"• {topic}" for topic in topics)
    if summary.decisions:
        lines.append("")
        lines.append("Decisions:")
        for decision in summary.decisions:
            speaker = decision.speaker.strip() if decision.speaker else ""
            who = f", {speaker}" if speaker else ""
            lines.append(f"• {decision.text.strip()} ({decision.timestamp}{who})")
    if summary.action_items:
        lines.append("")
        lines.append("Action items:")
        for action in summary.action_items:
            owner = action.owner_name.strip() if action.owner_name else ""
            due = f", due {format_absolute(action.due)}" if action.due else ""
            lines.append(
                f"• {action.task.strip()} — {owner or 'unassigned'}{due} "
                f"({action.timestamp})"
            )
    questions = [question.strip() for question in summary.open_questions if question.strip()]
    if questions:
        lines.append("")
        lines.append("Open questions:")
        lines.extend(f"• {question}" for question in questions)
    if not summary.decisions and not summary.action_items:
        lines.append("")
        lines.append(NOTHING)
    return "\n".join(lines).strip()


def _keyboard(pending: _Pending) -> InlineKeyboardMarkup | None:
    rows = []
    for index, decision in enumerate(pending.decisions):
        if index in pending.logged:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    f"📝 Log: {_short(decision.text)}",
                    callback_data=f"mtg_log:{index}",
                )
            ]
        )
    for index, action in enumerate(pending.actions):
        if index in pending.added:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    f"📌 Add task: {_short(action.task)}",
                    callback_data=f"mtg_add:{index}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows) if rows else None


def _match_owners(
    chat_db_id: int, actions: list[MeetingActionItem]
) -> list[tuple[int | None, str | None]]:
    with SessionLocal() as session:
        users = list(
            session.scalars(
                select(User)
                .join(ChatMember, ChatMember.user_id == User.id)
                .where(ChatMember.chat_id == chat_db_id)
            )
        )
    by_name: dict[str, User] = {}
    for user in users:
        if user.first_name:
            by_name.setdefault(user.first_name.strip().lower(), user)
        if user.username:
            by_name.setdefault(user.username.strip().lower(), user)
    owners = []
    for action in actions:
        owner = None
        if action.owner_name and action.owner_name.strip():
            owner = by_name.get(action.owner_name.strip().lower())
        if owner is None:
            owners.append((None, None))
        else:
            display = owner.first_name or owner.username or action.owner_name
            owners.append((owner.telegram_user_id, display))
    return owners


async def _read_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    document = message.document
    source_date = message.date
    if document is None and message.reply_to_message is not None:
        reply = message.reply_to_message
        document = reply.document
        if document is not None:
            source_date = reply.date
        elif reply.text:
            return reply.text, reply.date, False
    if document is not None:
        name = (document.file_name or "").lower()
        if not (name.endswith(".vtt") or name.endswith(".txt")):
            raise TranscriptError(UNSUPPORTED)
        if document.file_size and document.file_size > MAX_FILE_BYTES:
            raise TranscriptError(_too_long_message(document.file_size // 6))
        tg_file = await context.bot.get_file(document.file_id)
        data = await tg_file.download_as_bytearray()
        try:
            text = bytes(data).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TranscriptError(DECODE_ERROR) from exc
        return text, source_date, name.endswith(".vtt")
    args = " ".join(context.args or []).strip()
    if args:
        return args, message.date, False
    return None


async def meeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    if chat.type == ChatType.PRIVATE:
        await message.reply_text(PRIVATE)
        return

    try:
        source = await _read_source(update, context)
    except TranscriptError as exc:
        await message.reply_text(str(exc))
        return
    except Exception:
        logger.exception("Failed to read meeting transcript")
        await message.reply_text(FAILED)
        return
    if source is None:
        await message.reply_text(USAGE)
        return

    raw, source_date, is_vtt = source
    if len(raw) > MAX_TRANSCRIPT_CHARS:
        await message.reply_text(_too_long_message(_word_count(raw)))
        return
    cleaned = clean_vtt(raw) if is_vtt else raw.strip()
    if not cleaned:
        await message.reply_text(EMPTY_FILE)
        return
    if len(cleaned) > MAX_TRANSCRIPT_CHARS:
        await message.reply_text(_too_long_message(_word_count(cleaned)))
        return

    started = time.monotonic()
    try:
        summary = await summarize_meeting(cleaned, source_date)
    except MeetingTooLongError:
        await message.reply_text(_too_long_message(_word_count(cleaned)))
        return
    except MeetingTimeoutError:
        await message.reply_text(TIMEOUT)
        return
    except Exception:
        logger.exception("Meeting summary failed for chat %s", chat.id)
        await message.reply_text(FAILED)
        return
    if summary is None:
        await message.reply_text(FAILED)
        return
    _ground(summary, cleaned)
    logger.info("Meeting summary in %.2fs for chat %s", time.monotonic() - started, chat.id)

    with SessionLocal() as session:
        db_chat = get_or_create_chat(session, chat.id, chat.title)
        db_user = get_or_create_user(
            session, user.id, user.username, user.first_name
        )
        get_or_create_chat_member(session, db_chat.id, db_user.id)
        session.commit()
        chat_db_id = db_chat.id
        requester_db_id = db_user.id

    owners = _match_owners(chat_db_id, summary.action_items)
    pending = _Pending(
        chat_db_id=chat_db_id,
        decisions=summary.decisions,
        actions=summary.action_items,
        owners=owners,
    )
    text = _render(summary, source_date)
    sent = await message.reply_text(text, reply_markup=_keyboard(pending))
    _pending[(chat.id, sent.message_id)] = pending

    with SessionLocal() as session:
        session.add(
            Message(
                chat_id=chat_db_id,
                user_id=requester_db_id,
                telegram_message_id=sent.message_id,
                text=text,
                sent_at=source_date,
            )
        )
        session.commit()


async def handle_meeting_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return
    action, _, raw = query.data.partition(":")
    try:
        index = int(raw)
    except ValueError:
        await query.answer()
        return

    key = (query.message.chat_id, query.message.message_id)
    pending = _pending.get(key)
    if pending is None:
        await query.answer(
            "This summary has expired. Send the transcript again to log it.",
            show_alert=True,
        )
        return

    if action == "mtg_log":
        await _log_decision(query, pending, index)
    elif action == "mtg_add":
        await _add_task(query, pending, index)
    else:
        await query.answer()


async def _log_decision(query, pending: _Pending, index: int) -> None:
    if index < 0 or index >= len(pending.decisions):
        await query.answer()
        return
    if index in pending.logged:
        await query.answer("Already logged.")
        return

    decision = pending.decisions[index]
    with SessionLocal() as session:
        db_user = get_or_create_user(
            session,
            query.from_user.id,
            query.from_user.username,
            query.from_user.first_name,
        )
        session.add(
            Decision(
                chat_id=pending.chat_db_id,
                user_id=db_user.id,
                text=decision.text.strip(),
                telegram_message_id=query.message.message_id,
            )
        )
        session.commit()
    pending.logged.add(index)
    await _refresh_buttons(query, pending)
    await query.answer("Logged")


async def _add_task(query, pending: _Pending, index: int) -> None:
    if index < 0 or index >= len(pending.actions):
        await query.answer()
        return
    if index in pending.added:
        await query.answer("Already added.")
        return

    owner_telegram_id, owner_name = pending.owners[index]
    if owner_telegram_id is not None and query.from_user.id != owner_telegram_id:
        await query.answer(
            f"Only {owner_name} can confirm this.", show_alert=True
        )
        return

    item = pending.actions[index]
    with SessionLocal() as session:
        owner_db_id = None
        if owner_telegram_id is not None:
            owner = session.scalar(
                select(User).where(User.telegram_user_id == owner_telegram_id)
            )
            owner_db_id = owner.id if owner else None
        task = Task(
            chat_id=pending.chat_db_id,
            owner_user_id=owner_db_id,
            title=item.task.strip(),
            due_at=item.due,
            status="open",
            source="ai",
            confirm_message_id=query.message.message_id,
        )
        session.add(task)
        session.flush()
        if owner_db_id is not None:
            create_reminders(session, task)
        session.commit()
    pending.added.add(index)
    await _refresh_buttons(query, pending)
    await query.answer("Added")


async def _refresh_buttons(query, pending: _Pending) -> None:
    try:
        await query.edit_message_reply_markup(reply_markup=_keyboard(pending))
    except Exception:
        logger.exception("Failed to update meeting minutes buttons")
