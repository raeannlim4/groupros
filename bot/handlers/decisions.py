from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.db import SessionLocal, get_or_create_chat, get_or_create_user
from bot.models import Decision

logger = logging.getLogger(__name__)

USAGE = "Usage: /decide <the decision>"


async def decide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
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
