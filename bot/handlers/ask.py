from __future__ import annotations

import html
import logging
import re

from sqlalchemy import func, select
from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from bot import config, links
from bot.db import SessionLocal, get_or_create_chat, get_or_create_user
from bot.embeddings import embed_texts
from bot.llm import answer_question
from bot.models import Decision, Message, User
from bot.rag.retrieve import search_chunks
from bot.timeutil import format_absolute

logger = logging.getLogger(__name__)

USAGE = "Usage: /ask <question>"
DECISION_LIMIT = 20
NOT_FOUND = "I couldn't find that in this group's chat or decisions."
PRIVATE_REPLY = "Use /ask in a group chat."
CITATION_RE = re.compile(r"\[\[(\d+)\]\]")
SPACE_RE = re.compile(r" {2,}")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,;:!?])")


def _user_name(user: User | None) -> str:
    if user is None:
        return "someone"
    return user.first_name or user.username or "someone"


def _has_decisions(session, chat_id: int) -> bool:
    count = session.scalar(
        select(func.count()).select_from(Decision).where(Decision.chat_id == chat_id)
    )
    return bool(count)


def build_context(session, chat_id: int, chunks):
    parts: list[str] = []
    sources: dict[int, object] = {}

    decisions = list(
        session.scalars(
            select(Decision)
            .where(Decision.chat_id == chat_id)
            .order_by(Decision.created_at.desc())
            .limit(DECISION_LIMIT)
        )
    )
    if decisions:
        user_ids = {decision.user_id for decision in decisions}
        users = {
            user.id: user
            for user in session.scalars(select(User).where(User.id.in_(user_ids)))
        }
        lines = ["DECISIONS (agreed by the group):"]
        for decision in decisions:
            sources[decision.telegram_message_id] = decision.created_at
            lines.append(
                f"[{decision.telegram_message_id}] "
                f"{format_absolute(decision.created_at)} "
                f"{_user_name(users.get(decision.user_id))} decided: "
                f"{decision.text}"
            )
        parts.append("\n".join(lines))

    for chunk in chunks:
        parts.append(chunk.text)
        rows = session.scalars(
            select(Message).where(
                Message.chat_id == chat_id,
                Message.id >= chunk.first_message_id,
                Message.id <= chunk.last_message_id,
            )
        )
        for message in rows:
            sources[message.telegram_message_id] = message.sent_at

    return "\n\n".join(parts), sources


def render_answer(
    answer: str, telegram_chat_id: int, sources: dict[int, object]
) -> tuple[str, int]:
    valid = 0

    def replace(match: re.Match) -> str:
        nonlocal valid
        message_id = int(match.group(1))
        if message_id not in sources:
            return ""
        valid += 1
        link = links.message_link(telegram_chat_id, message_id)
        if link:
            return f'<a href="{link}">{message_id}</a>'
        sent_at = sources[message_id]
        return format_absolute(sent_at) if sent_at is not None else str(message_id)

    escaped = html.escape(answer, quote=False)
    rendered = CITATION_RE.sub(replace, escaped)
    rendered = SPACE_RE.sub(" ", rendered)
    rendered = SPACE_BEFORE_PUNCT_RE.sub(r"\1", rendered)
    return rendered.strip(), valid


async def answer_for_chat(
    chat_db_id: int, telegram_chat_id: int, question: str
) -> str:
    embedding = (await embed_texts([question]))[0]
    results = await search_chunks(chat_db_id, embedding)
    logger.debug(
        "ASK distances chat %s: %s",
        chat_db_id,
        [round(distance, 4) for _, distance in results],
    )

    with SessionLocal() as session:
        has_decisions = _has_decisions(session, chat_db_id)

    best_distance = results[0][1] if results else None
    if not has_decisions and (
        best_distance is None or best_distance > config.ASK_MAX_DISTANCE
    ):
        return NOT_FOUND

    with SessionLocal() as session:
        context_text, sources = build_context(
            session, chat_db_id, [chunk for chunk, _ in results]
        )
    logger.debug("ASK context for chat %s:\n%s", chat_db_id, context_text)

    answer = await answer_question(question, context_text)
    rendered, valid = render_answer(answer, telegram_chat_id, sources)
    if valid == 0:
        return NOT_FOUND
    return rendered


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return

    if chat.type == ChatType.PRIVATE:
        await message.reply_text(PRIVATE_REPLY)
        return

    question = " ".join(context.args).strip()
    if not question:
        await message.reply_text(USAGE)
        return

    with SessionLocal() as session:
        db_chat = get_or_create_chat(session, chat.id, chat.title)
        get_or_create_user(session, user.id, user.username, user.first_name)
        session.commit()
        chat_db_id = db_chat.id
        telegram_chat_id = db_chat.telegram_chat_id

    try:
        rendered = await answer_for_chat(chat_db_id, telegram_chat_id, question)
        await message.reply_text(rendered, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("ask failed for chat %s", chat_db_id)
        await message.reply_text("Sorry, something went wrong, try again.")
