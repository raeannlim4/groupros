from __future__ import annotations

import html
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import ContextTypes

from bot.db import SessionLocal
from bot.models import Chat, ChatMember, Reminder, Task, User
from bot.timeutil import (
    format_absolute,
    format_relative,
    get_tz,
    now_utc,
    to_local,
)

logger = logging.getLogger(__name__)

DAY_BEFORE = timedelta(hours=24)
DUE_SOON = timedelta(hours=3)


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _enable_button(bot_username: str | None) -> InlineKeyboardButton | None:
    if not bot_username:
        return None
    return InlineKeyboardButton(
        "Enable private reminders",
        url=f"https://t.me/{bot_username}?start=enable",
    )


def create_reminders(session, task) -> None:
    """(Re)build the unsent reminders for a task's current deadline."""
    now = now_utc()
    for reminder in list(
        session.scalars(
            select(Reminder).where(
                Reminder.task_id == task.id, Reminder.sent_at.is_(None)
            )
        )
    ):
        session.delete(reminder)

    if task.due_at is None:
        return

    for kind, delta in (("day_before", DAY_BEFORE), ("due_soon", DUE_SOON)):
        remind_at = task.due_at - delta
        if remind_at > now:
            session.add(
                Reminder(task_id=task.id, remind_at=remind_at, kind=kind)
            )


def backfill_reminders() -> None:
    with SessionLocal() as session:
        tasks = list(
            session.scalars(
                select(Task).where(
                    Task.status == "open", Task.due_at.is_not(None)
                )
            )
        )
        for task in tasks:
            create_reminders(session, task)
        session.commit()
    logger.info("Backfilled reminders for %d open tasks.", len(tasks))


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = now_utc()
    with SessionLocal() as session:
        reminders = list(
            session.scalars(
                select(Reminder).where(
                    Reminder.remind_at <= now, Reminder.sent_at.is_(None)
                )
            )
        )
        for reminder in reminders:
            task = session.get(Task, reminder.task_id)
            if task is None or task.status != "open":
                session.delete(reminder)
                continue
            owner = (
                session.get(User, task.owner_user_id)
                if task.owner_user_id
                else None
            )
            chat = session.get(Chat, task.chat_id)
            if owner is None or chat is None:
                session.delete(reminder)
                continue
            await _send_reminder(context, session, reminder, task, owner, chat, now)
        session.commit()


async def _send_reminder(context, session, reminder, task, owner, chat, now) -> None:
    text = f'Hi! "{_escape(task.title)}" is due {format_relative(task.due_at, now)}.'
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "👍 On track", callback_data=f"rem_ontrack:{task.id}"
                ),
                InlineKeyboardButton(
                    "⏰ Need more time", callback_data=f"rem_more:{task.id}"
                ),
            ]
        ]
    )
    try:
        await context.bot.send_message(
            chat_id=owner.telegram_user_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        reminder.sent_at = now
        return
    except Forbidden:
        owner.can_dm = False
        reminder.sent_at = now
        logger.info("Cannot DM user %s; marked reminder sent.", owner.telegram_user_id)
    except Exception:
        logger.exception("Failed to send reminder %s", reminder.id)
        return

    await _send_dm_nudge(context, session, owner, chat)


async def _send_dm_nudge(context, session, owner, chat) -> None:
    member = session.scalar(
        select(ChatMember).where(
            ChatMember.chat_id == chat.id, ChatMember.user_id == owner.id
        )
    )
    if member is None or member.nudged_for_dm:
        return

    button = _enable_button(context.bot.username)
    if button is None:
        return

    name = f"@{owner.username}" if owner.username else _escape(
        owner.first_name or "there"
    )
    try:
        await context.bot.send_message(
            chat_id=chat.telegram_chat_id,
            text=f"{name}, tap below to get private deadline reminders",
            reply_markup=InlineKeyboardMarkup([[button]]),
        )
        member.nudged_for_dm = True
    except Exception:
        logger.exception("Failed to post DM nudge for chat %s", chat.telegram_chat_id)


def _day_label(day) -> str:
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def _day_picker_rows(task_id: int, current_due):
    today = to_local(now_utc()).date()
    tz = get_tz()
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for offset in range(1, 8):
        day = today + timedelta(days=offset)
        deadline = datetime(day.year, day.month, day.day, 23, 59, tzinfo=tz)
        if current_due is not None and deadline <= current_due:
            continue
        row.append(
            InlineKeyboardButton(
                _day_label(day),
                callback_data=f"rem_day:{task_id}:{day.isoformat()}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [InlineKeyboardButton("← Back", callback_data=f"rem_more:{task_id}")]
    )
    return rows


async def _apply_new_deadline(query, task_id: int, new_due) -> None:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if task is None or task.status != "open":
            await query.answer("This task is no longer open.")
            return
        if task.due_at is not None and new_due <= task.due_at:
            await query.answer(
                "That deadline is not later than the current one.",
                show_alert=True,
            )
            return
        task.due_at = new_due
        create_reminders(session, task)
        session.commit()
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Tell the group", callback_data=f"rem_tell_yes:{task_id}"
                ),
                InlineKeyboardButton(
                    "Keep private", callback_data=f"rem_tell_no:{task_id}"
                ),
            ]
        ]
    )
    await query.edit_message_text(
        f"Now due {format_absolute(new_due)}.\nTell the group?",
        reply_markup=keyboard,
    )
    await query.answer()


async def handle_reminder_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return

    parts = query.data.split(":")
    action = parts[0]
    try:
        task_id = int(parts[1])
    except (IndexError, ValueError):
        await query.answer()
        return

    now = now_utc()

    if action == "rem_ontrack":
        await query.edit_message_text("Great, good luck!")
        await query.answer()
        return

    if action == "rem_more":
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            title = task.title if task is not None else "this task"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "+1 day", callback_data=f"rem_plus1:{task_id}"
                    ),
                    InlineKeyboardButton(
                        "+2 days", callback_data=f"rem_plus2:{task_id}"
                    ),
                    InlineKeyboardButton(
                        "+1 week", callback_data=f"rem_plus7:{task_id}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "📅 Pick a day", callback_data=f"rem_pick:{task_id}"
                    )
                ],
            ]
        )
        await query.edit_message_text(
            f'⏰ Need more time: "{_escape(title)}"\nPick a new deadline:',
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        await query.answer()
        return

    if action == "rem_pick":
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if task is None:
                await query.answer("This task no longer exists.")
                return
            title = task.title
            current_due = task.due_at
        await query.edit_message_text(
            f'📅 Pick a day for "{_escape(title)}":',
            reply_markup=InlineKeyboardMarkup(
                _day_picker_rows(task_id, current_due)
            ),
            parse_mode=ParseMode.HTML,
        )
        await query.answer()
        return

    if action == "rem_day":
        if len(parts) < 3:
            await query.answer()
            return
        try:
            day = date.fromisoformat(parts[2])
        except ValueError:
            await query.answer()
            return
        tz = get_tz()
        new_due = datetime(day.year, day.month, day.day, 23, 59, tzinfo=tz)
        await _apply_new_deadline(query, task_id, new_due)
        return

    if action in ("rem_plus1", "rem_plus2", "rem_plus7"):
        days = {"rem_plus1": 1, "rem_plus2": 2, "rem_plus7": 7}[action]
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if task is None or task.status != "open":
                await query.answer("This task is no longer open.")
                return
            base = task.due_at if task.due_at and task.due_at > now else now
            new_due = base + timedelta(days=days)
        await _apply_new_deadline(query, task_id, new_due)
        return

    if action == "rem_tell_yes":
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            chat = session.get(Chat, task.chat_id) if task is not None else None
            title = task.title if task is not None else ""
            due = task.due_at if task is not None else None
        if chat is not None and due is not None:
            await context.bot.send_message(
                chat_id=chat.telegram_chat_id,
                text=(
                    f'🗓 Updated: "{_escape(title)}" is now due '
                    f"{format_absolute(due)}."
                ),
                parse_mode=ParseMode.HTML,
            )
        await query.edit_message_text("Posted to the group.")
        await query.answer()
        return

    if action == "rem_tell_no":
        await query.edit_message_text("Okay, kept private.")
        await query.answer()
        return

    await query.answer()