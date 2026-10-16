from __future__ import annotations

import logging
import time

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyParameters,
    Update,
)
from telegram.ext import ContextTypes

from bot.db import (
    SessionLocal,
    get_or_create_chat,
    get_or_create_chat_member,
    get_or_create_user,
)
from bot.handlers.reminders import create_reminders
from bot.llm import classify_commitment
from bot.models import Message, Task, User
from bot.timeutil import format_absolute, now_utc

logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 8
CONFIDENCE_THRESHOLD = 0.7


async def store_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or message.text is None or chat is None or user is None:
        return

    db_started = time.perf_counter()
    with SessionLocal() as session:
        db_chat = get_or_create_chat(session, chat.id, chat.title)
        db_user = get_or_create_user(
            session, user.id, user.username, user.first_name
        )
        get_or_create_chat_member(session, db_chat.id, db_user.id)
        session.add(
            Message(
                chat_id=db_chat.id,
                user_id=db_user.id,
                telegram_message_id=message.message_id,
                text=message.text,
                sent_at=message.date,
            )
        )
        session.commit()
        chat_db_id = db_chat.id
        owner_db_id = db_user.id
    stored = time.perf_counter()

    text = message.text
    if len(text.strip()) < MIN_TEXT_LENGTH:
        logger.info("capture: db %.2fs", stored - db_started)
        return

    sender_name = user.first_name or user.username or "Unknown"
    llm_started = time.perf_counter()
    commitment = await classify_commitment(text, sender_name, now_utc())
    classified = time.perf_counter()
    logger.info(
        "capture: db %.2fs, llm %.2fs",
        stored - db_started,
        classified - llm_started,
    )
    if (
        commitment is None
        or not commitment.is_commitment
        or commitment.confidence < CONFIDENCE_THRESHOLD
    ):
        return

    await _send_capture_prompt(
        message, chat_db_id, owner_db_id, sender_name, commitment
    )


async def _send_capture_prompt(
    original,
    chat_db_id: int,
    owner_db_id: int,
    owner_name: str,
    commitment,
) -> None:
    title = commitment.task.strip() or original.text.strip()
    due = commitment.due

    with SessionLocal() as session:
        task = Task(
            chat_id=chat_db_id,
            owner_user_id=owner_db_id,
            title=title,
            due_at=due,
            status="pending",
            source="ai",
            source_message_id=original.message_id,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    due_text = f", due {format_absolute(due)}" if due else ""
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Add", callback_data=f"cap_add:{task_id}"),
                InlineKeyboardButton("❌ No", callback_data=f"cap_no:{task_id}"),
            ]
        ]
    )
    sent = await original.reply_text(
        f"📌 Add task? {owner_name}: {title}{due_text}",
        reply_markup=keyboard,
        reply_parameters=ReplyParameters(message_id=original.message_id),
    )

    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if task is not None:
            task.confirm_message_id = sent.message_id
            session.commit()


async def handle_capture_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return

    action, _, raw_id = query.data.partition(":")
    try:
        task_id = int(raw_id)
    except ValueError:
        await query.answer()
        return

    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if task is None or task.status != "pending":
            await query.answer("This task was already handled.")
            return
        owner = session.get(User, task.owner_user_id) if task.owner_user_id else None
        owner_name = "the owner"
        owner_telegram_id = None
        if owner is not None:
            owner_telegram_id = owner.telegram_user_id
            owner_name = owner.first_name or owner.username or "the owner"
        task_title = task.title
        task_due = task.due_at

    if owner_telegram_id is not None and query.from_user.id != owner_telegram_id:
        await query.answer(
            f"Only {owner_name} can confirm this.", show_alert=True
        )
        return

    if action == "cap_add":
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if task is not None:
                task.status = "open"
                create_reminders(session, task)
                session.commit()
        due_text = f", due {format_absolute(task_due)}" if task_due else ""
        await query.edit_message_text(
            f"✅ Added: {task_title} ({owner_name}{due_text})"
        )
    else:
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if task is not None:
                task.status = "rejected"
                session.commit()
        try:
            await query.delete_message()
        except Exception:
            await query.edit_message_text("Okay, not added.")

    await query.answer()
