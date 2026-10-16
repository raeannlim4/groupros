from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from bot.db import (
    SessionLocal,
    get_or_create_chat,
    get_or_create_chat_member,
    get_or_create_user,
)
from bot.handlers.ask import render_answer
from bot.llm import catchup_summary
from bot.models import Chat, ChatMember, Decision, Message, User
from bot.timeutil import format_absolute, format_compact, now_utc

logger = logging.getLogger(__name__)

USAGE = "Usage: /catchup [here] [window, e.g. 2d]"
PRIVATE_REPLY = "Use /catchup in a group chat."
EMPTY = "Not much happened, you're up to date."
FAILED = "Sorry, something went wrong, try again."
MIN_MESSAGES = 5
DEFAULT_WINDOW = timedelta(hours=24)
MAX_CONTEXT_CHARS = 100_000
DECISION_LIMIT = 20
MAX_BULLETS = 6

WINDOW_RE = re.compile(r"^(\d+)\s*([a-z]*)$", re.IGNORECASE)
UNITS = {
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "wk": 604800, "wks": 604800, "week": 604800, "weeks": 604800,
}
BULLET_RE = re.compile(r"^\s*[-•]\s*")
OFFTOPIC_RE = re.compile(r"^offtopic:\s*", re.IGNORECASE)
NEEDS_RE = re.compile(r"needs your input", re.IGNORECASE)


def _parse_window(arg: str) -> timedelta | None:
    match = WINDOW_RE.match(arg.strip())
    if match is None:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if amount <= 0 or unit not in UNITS:
        return None
    return timedelta(seconds=amount * UNITS[unit])


def _parse_args(arg: str) -> tuple[timedelta, bool] | None:
    """Return (window, here). `here` posts in the group. None means bad input."""
    tokens = arg.split()
    here = "here" in tokens
    remaining = " ".join(token for token in tokens if token != "here").strip()
    window = DEFAULT_WINDOW
    if remaining:
        parsed = _parse_window(remaining)
        if parsed is None:
            return None
        window = parsed
    return window, here


def _user_name(user: User | None) -> str:
    if user is None:
        return "someone"
    return user.first_name or user.username or "someone"


def _cap_lines(lines: list[str]) -> list[str]:
    """Keep the most recent lines that fit within the character cap."""
    selected: list[str] = []
    total = 0
    for line in reversed(lines):
        total += len(line) + 1
        if total > MAX_CONTEXT_CHARS and selected:
            break
        selected.append(line)
    selected.reverse()
    return selected


def _build_context(session, chat_id: int, window_start: datetime):
    """Return (message_count, context_text, sources) for the window."""
    messages = list(
        session.scalars(
            select(Message)
            .where(Message.chat_id == chat_id, Message.sent_at >= window_start)
            .order_by(Message.sent_at)
        )
    )
    user_ids = {message.user_id for message in messages}
    users = (
        {
            user.id: user
            for user in session.scalars(select(User).where(User.id.in_(user_ids)))
        }
        if user_ids
        else {}
    )

    sources: dict[int, object] = {}
    transcript: list[str] = []
    for message in messages:
        sources[message.telegram_message_id] = message.sent_at
        transcript.append(
            f"[{message.telegram_message_id}] {format_compact(message.sent_at)} "
            f"{_user_name(users.get(message.user_id))}: {message.text}"
        )

    decisions = list(
        session.scalars(
            select(Decision)
            .where(Decision.chat_id == chat_id, Decision.created_at >= window_start)
            .order_by(Decision.created_at.desc())
            .limit(DECISION_LIMIT)
        )
    )

    parts: list[str] = []
    if decisions:
        decision_user_ids = {decision.user_id for decision in decisions}
        decision_users = {
            user.id: user
            for user in session.scalars(
                select(User).where(User.id.in_(decision_user_ids))
            )
        }
        lines = ["DECISIONS (agreed by the group):"]
        for decision in decisions:
            sources[decision.telegram_message_id] = decision.created_at
            lines.append(
                f"[{decision.telegram_message_id}] "
                f"{format_absolute(decision.created_at)} "
                f"{_user_name(decision_users.get(decision.user_id))} decided: "
                f"{decision.text}"
            )
        parts.append("\n".join(lines))
    if transcript:
        parts.append("CHAT MESSAGES:\n" + "\n".join(_cap_lines(transcript)))

    return len(messages), "\n\n".join(parts), sources


def _set_can_dm_false(telegram_user_id: int) -> None:
    with SessionLocal() as session:
        user = session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        if user is not None:
            user.can_dm = False
            session.commit()


def _mark_catchup(telegram_chat_id: int, telegram_user_id: int, when: datetime) -> None:
    with SessionLocal() as session:
        chat = session.scalar(
            select(Chat).where(Chat.telegram_chat_id == telegram_chat_id)
        )
        user = session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        if chat is None or user is None:
            return
        member = session.scalar(
            select(ChatMember).where(
                ChatMember.chat_id == chat.id, ChatMember.user_id == user.id
            )
        )
        if member is not None:
            member.last_catchup_at = when
            session.commit()


def _render_bullets(
    summary: str, chat_id: int, sources: dict, reader_name: str, in_group: bool
) -> list[str]:
    """Turn the raw LLM summary into display bullets with citations rendered."""
    bullets: list[str] = []
    name = html.escape(reader_name, quote=False)
    for line in summary.splitlines():
        line = line.strip()
        if not line:
            continue
        line = BULLET_RE.sub("", line).strip()
        if not line:
            continue
        offtopic = OFFTOPIC_RE.match(line)
        if offtopic:
            line = line[offtopic.end():].strip()
        rendered, _valid = render_answer(line, chat_id, sources)
        if not rendered:
            continue
        if in_group:
            rendered = NEEDS_RE.sub(f"Needs {name}'s input", rendered)
        bullets.append(f"• {rendered}")
        if len(bullets) >= MAX_BULLETS:
            break
    return bullets


async def _deliver(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    in_group: bool = False,
) -> None:
    """DM the text when the user can receive DMs, unless in_group forces a reply."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    with SessionLocal() as session:
        db_user = session.scalar(
            select(User).where(User.telegram_user_id == user.id)
        )
        can_dm = db_user.can_dm if db_user is not None else False

    if not in_group and can_dm:
        try:
            await context.bot.send_message(
                chat_id=user.id, text=text, parse_mode=ParseMode.HTML
            )
            return
        except Forbidden:
            _set_can_dm_false(user.id)
            logger.info("Cannot DM user %s; replying in group.", user.id)
        except Exception:
            logger.exception("Failed to DM catchup to user %s", user.id)

    await message.reply_text(text, parse_mode=ParseMode.HTML)


async def catchup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return

    if chat.type == ChatType.PRIVATE:
        await message.reply_text(PRIVATE_REPLY)
        return

    arg = " ".join(context.args).strip()
    window = DEFAULT_WINDOW
    here = False
    if arg:
        parsed_args = _parse_args(arg)
        if parsed_args is None:
            await message.reply_text(USAGE)
            return
        window, here = parsed_args

    now = now_utc()
    reader_name = user.first_name or user.username or "there"

    with SessionLocal() as session:
        db_chat = get_or_create_chat(session, chat.id, chat.title)
        db_user = get_or_create_user(
            session, user.id, user.username, user.first_name
        )
        member = get_or_create_chat_member(session, db_chat.id, db_user.id)
        if arg:
            window_start = now - window
        elif member.last_catchup_at is not None:
            window_start = member.last_catchup_at
        else:
            window_start = now - DEFAULT_WINDOW
        can_dm = db_user.can_dm
        count, context_text, sources = _build_context(
            session, db_chat.id, window_start
        )
        session.commit()

    in_group = here or not can_dm

    if count < MIN_MESSAGES:
        await _deliver(update, context, EMPTY, in_group=in_group)
        _mark_catchup(chat.id, user.id, now)
        return

    try:
        summary = await catchup_summary(reader_name, context_text)
    except Exception:
        logger.exception("catchup summary failed for chat %s", chat.id)
        await message.reply_text(FAILED)
        return

    bullets = _render_bullets(summary, chat.id, sources, reader_name, in_group)
    if not bullets:
        logger.warning("catchup produced no bullets for chat %s", chat.id)
        await message.reply_text(FAILED)
        return

    await _deliver(update, context, "\n".join(bullets), in_group=in_group)
    _mark_catchup(chat.id, user.id, now)
