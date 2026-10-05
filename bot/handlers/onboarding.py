from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from bot import config
from bot.db import SessionLocal, get_or_create_chat, get_or_create_user

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Here is what I can do:\n"
    "/tasks – who is doing what and what is due\n"
    "/decide <text> – log a group decision\n"
    "/ask <question> – ask about past discussion\n"
    "/catchup – catch up on what you missed\n"
    "/forget – delete your data\n"
    "/help – show this message"
)

INTRO_TEXT = (
    "Hi! I help this group stay on top of tasks.\n\n"
    "I notice when someone commits to something and ask before adding it as a "
    "task. You can see the board any time with /tasks, get private deadline "
    "reminders by DM, and log decisions with /decide.\n\n"
    "I store group text messages, tasks and decisions. Messages are processed "
    "by AI providers so I can do this. Anyone can delete their data anytime "
    "with /forget."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if update.effective_chat.type == ChatType.PRIVATE:
        with SessionLocal() as session:
            db_user = get_or_create_user(
                session, user.id, user.username, user.first_name
            )
            db_user.can_dm = True
            session.commit()
        name = user.first_name or "there"
        await message.reply_text(
            f"Hi {name}! You'll now get private deadline reminders here. "
            "Tap a reminder button to react, or use /forget anytime to delete "
            "your data."
        )
    else:
        await message.reply_text(HELP_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(HELP_TEXT)


def _enable_button(bot_username: str | None) -> InlineKeyboardButton | None:
    if not bot_username:
        return None
    return InlineKeyboardButton(
        "Enable private reminders",
        url=f"https://t.me/{bot_username}?start=enable",
    )


async def bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.new_chat_members:
        return

    bot_id = context.bot.id
    if not any(member.id == bot_id for member in message.new_chat_members):
        return

    with SessionLocal() as session:
        get_or_create_chat(session, chat.id, chat.title)
        session.commit()

    rows = []
    enable = _enable_button(context.bot.username)
    if enable is not None:
        rows.append([enable])
    if config.PRIVACY_URL:
        rows.append([InlineKeyboardButton("Privacy info", url=config.PRIVACY_URL)])

    kwargs = {}
    if rows:
        kwargs["reply_markup"] = InlineKeyboardMarkup(rows)

    await message.reply_text(INTRO_TEXT, parse_mode=ParseMode.HTML, **kwargs)
    logger.info("Added to chat %s (%s).", chat.id, chat.title)
