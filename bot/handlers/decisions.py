from __future__ import annotations

import html
import logging

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from bot import links
from bot.db import SessionLocal, get_or_create_chat, get_or_create_user
from bot.handlers.onboarding import take_link_tip
from bot.models import Chat, Decision, User
from bot.timeutil import format_absolute

logger = logging.getLogger(__name__)

USAGE = "Usage: /decide <the decision>"
NO_DECISIONS = "No decisions logged yet. Use /decide <text> to log one."
SHORT_TEXT_LENGTH = 18
DECISION_LIMIT = 10


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _short(text: str) -> str:
    text = text.strip()
    if len(text) <= SHORT_TEXT_LENGTH:
        return text
    return text[: SHORT_TEXT_LENGTH - 1].rstrip() + "…"


def _user_name(user: User | None) -> str:
    if user is None:
        return "someone"
    return user.first_name or user.username or "someone"


def build_decisions(chat_db_id: int):
    with SessionLocal() as session:
        decisions = list(
            session.scalars(
                select(Decision)
                .where(Decision.chat_id == chat_db_id)
                .order_by(Decision.created_at.desc(), Decision.id.desc())
                .limit(DECISION_LIMIT)
            )
        )
        if not decisions:
            return NO_DECISIONS, None

        chat = session.get(Chat, chat_db_id)
        user_ids = {decision.user_id for decision in decisions}
        users = {
            user.id: user
            for user in session.scalars(select(User).where(User.id.in_(user_ids)))
        }

        lines: list[str] = []
        rows: list[list[InlineKeyboardButton]] = []
        for decision in decisions:
            name = _escape(_user_name(users.get(decision.user_id)))
            text = _escape(decision.text)
            link = (
                links.message_link(chat.telegram_chat_id, decision.telegram_message_id)
                if chat is not None
                else None
            )
            if link:
                text = f'<a href="{link}">{text}</a>'
            date = format_absolute(decision.created_at)
            lines.append(f"• {text} — {name}, {date}")
            rows.append(
                [
                    InlineKeyboardButton(
                        f"🗑 {_short(decision.text)}",
                        callback_data=f"dec_rm:{decision.id}",
                    )
                ]
            )

        return "\n".join(lines), InlineKeyboardMarkup(rows)


async def decide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return

    if chat.type == ChatType.PRIVATE:
        await message.reply_text("Use /decide in a group chat.")
        return

    text = " ".join(context.args).strip()
    if not text:
        await message.reply_text(USAGE)
        return

    with SessionLocal() as session:
        db_chat = get_or_create_chat(session, chat.id, chat.title)
        db_user = get_or_create_user(session, user.id, user.username, user.first_name)
        session.add(
            Decision(
                chat_id=db_chat.id,
                user_id=db_user.id,
                text=text,
                telegram_message_id=message.message_id,
            )
        )
        session.commit()

    await message.reply_text(f"📝 Logged: {text}")


async def decisions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return

    if chat.type == ChatType.PRIVATE:
        await message.reply_text("Use /decisions in a group chat.")
        return

    with SessionLocal() as session:
        db_chat = get_or_create_chat(session, chat.id, chat.title)
        get_or_create_user(session, user.id, user.username, user.first_name)
        session.commit()
        chat_db_id = db_chat.id

    text, keyboard = build_decisions(chat_db_id)
    tip = take_link_tip(chat_db_id, chat.type)
    if tip:
        text = f"{text}\n\n{tip}"
    await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def handle_decision_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return

    _, _, raw_id = query.data.partition(":")
    try:
        decision_id = int(raw_id)
    except ValueError:
        await query.answer()
        return

    with SessionLocal() as session:
        decision = session.get(Decision, decision_id)
        if decision is None:
            await query.answer("This decision is already gone.")
            return
        author = session.get(User, decision.user_id)
        chat_db_id = decision.chat_id
        if author is None or author.telegram_user_id != query.from_user.id:
            name = _escape(_user_name(author)) if author is not None else "the author"
            await query.answer(f"Only {name} can delete this.", show_alert=True)
            return
        session.delete(decision)
        session.commit()

    text, keyboard = build_decisions(chat_db_id)
    try:
        await query.edit_message_text(
            text, reply_markup=keyboard, parse_mode=ParseMode.HTML
        )
    except Exception:
        logger.exception("Failed to refresh decisions list")
    await query.answer()
