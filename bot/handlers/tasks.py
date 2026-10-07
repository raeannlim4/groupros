from __future__ import annotations

import html
import logging
from datetime import timedelta

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from bot.db import SessionLocal, get_or_create_chat, get_or_create_user
from bot.handlers.reminders import create_reminders
from bot.models import Chat, Task, User
from bot.timeutil import format_absolute, now_utc

logger = logging.getLogger(__name__)

BOARD_LIMIT = 4000
SHORT_TITLE_LENGTH = 18


def _short(title: str) -> str:
    title = title.strip()
    if len(title) <= SHORT_TITLE_LENGTH:
        return title
    return title[: SHORT_TITLE_LENGTH - 1].rstrip() + "…"


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _owner_name(session, task) -> str:
    if task.owner_user_id is None:
        return "unclaimed"
    owner = session.get(User, task.owner_user_id)
    if owner is None:
        return "someone"
    return owner.first_name or owner.username or "someone"


def _line(session, task, is_done: bool) -> str:
    name = _owner_name(session, task)
    due = f" (due {format_absolute(task.due_at)})" if task.due_at else ""
    check = " ✓" if is_done else ""
    return f"• {_escape(task.title)} — {_escape(name)}{due}{check}"


def _bucket(open_tasks: list[Task], now):
    overdue: list[Task] = []
    soon: list[Task] = []
    later: list[Task] = []
    no_due: list[Task] = []
    unclaimed: list[Task] = []
    cutoff = now + timedelta(hours=48)

    for task in open_tasks:
        if task.owner_user_id is None:
            unclaimed.append(task)
        elif task.due_at is None:
            no_due.append(task)
        elif task.due_at < now:
            overdue.append(task)
        elif task.due_at <= cutoff:
            soon.append(task)
        else:
            later.append(task)

    overdue.sort(key=lambda t: t.due_at)
    soon.sort(key=lambda t: t.due_at)
    later.sort(key=lambda t: t.due_at)
    unclaimed.sort(key=lambda t: (t.due_at is None, t.due_at or t.created_at))
    return overdue, soon, later, no_due, unclaimed


def build_board(chat_db_id: int, now):
    with SessionLocal() as session:
        open_tasks = list(
            session.scalars(
                select(Task).where(
                    Task.chat_id == chat_db_id, Task.status == "open"
                )
            )
        )
        done_tasks = list(
            session.scalars(
                select(Task)
                .where(Task.chat_id == chat_db_id, Task.status == "done")
                .order_by(Task.completed_at.desc())
                .limit(5)
            )
        )

        overdue, soon, later, no_due, unclaimed = _bucket(open_tasks, now)
        sections = [
            ("Overdue", overdue, False),
            ("Due in 48h", soon, False),
            ("Later", later, False),
            ("No deadline", no_due, False),
            ("Unclaimed", unclaimed, False),
            ("Done", done_tasks, True),
        ]

        entries = []
        first_section = True
        for header, items, is_done in sections:
            if not items:
                continue
            if not first_section:
                entries.append(("blank", "", None, None))
            first_section = False
            entries.append(("header", f"<b>{header}</b>", None, None))
            for task in items:
                entries.append(("task", _line(session, task, is_done), task, is_done))

        lines: list[str] = []
        rows: list[list[InlineKeyboardButton]] = []
        for index, (kind, text, task, is_done) in enumerate(entries):
            if lines and len("\n".join(lines + [text])) > BOARD_LIMIT:
                remaining = sum(
                    1 for k, _, _, _ in entries[index:] if k == "task"
                )
                if remaining:
                    lines.append(f"…and {remaining} more")
                break
            lines.append(text)
            if kind == "task" and not is_done:
                if task.owner_user_id is not None:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                f"✅ {_short(task.title)}",
                                callback_data=f"task_done:{task.id}",
                            )
                        ]
                    )
                else:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                f"🙋 Claim: {_short(task.title)}",
                                callback_data=f"task_claim:{task.id}",
                            )
                        ]
                    )

        text = "\n".join(lines) if lines else "No tasks yet."
        keyboard = InlineKeyboardMarkup(rows) if rows else None
        return text, keyboard


async def task_board(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return

    with SessionLocal() as session:
        db_chat = get_or_create_chat(session, chat.id, chat.title)
        get_or_create_user(session, user.id, user.username, user.first_name)
        session.commit()
        chat_db_id = db_chat.id

    text, keyboard = build_board(chat_db_id, now_utc())
    await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def handle_task_callback(
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

    user = query.from_user
    now = now_utc()

    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if task is None or task.status != "open":
            await query.answer("This task is no longer open.")
            return

        owner = session.get(User, task.owner_user_id) if task.owner_user_id else None
        if action == "task_done":
            owner_name = "the owner"
            if owner is not None:
                owner_name = owner.first_name or owner.username or "the owner"
            if owner is None or owner.telegram_user_id != user.id:
                await query.answer(
                    f"Only {owner_name} can complete this.", show_alert=True
                )
                return
            task.status = "done"
            task.completed_at = now
            chat_db_id = task.chat_id
            session.commit()
        else:
            if task.owner_user_id is not None:
                await query.answer("Someone already claimed this.")
                return
            db_user = get_or_create_user(
                session, user.id, user.username, user.first_name
            )
            session.flush()
            task.owner_user_id = db_user.id
            create_reminders(session, task)
            chat_db_id = task.chat_id
            session.commit()

    text, keyboard = build_board(chat_db_id, now)
    try:
        await query.edit_message_text(
            text, reply_markup=keyboard, parse_mode=ParseMode.HTML
        )
    except Exception:
        logger.exception("Failed to refresh task board")
    await query.answer()


def build_my_tasks(telegram_user_id: int):
    with SessionLocal() as session:
        db_user = session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        if db_user is None:
            return "You have no open tasks.", None

        tasks = list(
            session.scalars(
                select(Task).where(
                    Task.owner_user_id == db_user.id, Task.status == "open"
                )
            )
        )
        if not tasks:
            return "You have no open tasks.", None

        grouped: dict[int, list[Task]] = {}
        for task in tasks:
            grouped.setdefault(task.chat_id, []).append(task)

        chats = {
            chat.id: chat
            for chat in session.scalars(
                select(Chat).where(Chat.id.in_(grouped.keys()))
            )
        }

        entries: list[tuple[str, Task | None]] = []
        first_group = True
        for chat_id in sorted(
            grouped,
            key=lambda cid: (chats[cid].title or "").lower() if cid in chats else "",
        ):
            if not first_group:
                entries.append(("", None))
            first_group = False
            chat = chats.get(chat_id)
            title = _escape(chat.title) if chat and chat.title else "Group"
            entries.append((f"<b>{title}</b>", None))
            ordered = sorted(
                grouped[chat_id],
                key=lambda t: (t.due_at is None, t.due_at or t.created_at),
            )
            for task in ordered:
                due = (
                    f" (due {format_absolute(task.due_at)})" if task.due_at else ""
                )
                entries.append((f"• {_escape(task.title)}{due}", task))

        lines: list[str] = []
        rows: list[list[InlineKeyboardButton]] = []
        for index, (line, task) in enumerate(entries):
            if lines and len("\n".join(lines + [line])) > BOARD_LIMIT:
                remaining = sum(
                    1
                    for _, remaining_task in entries[index:]
                    if remaining_task is not None
                )
                if remaining:
                    lines.append(f"…and {remaining} more")
                break
            lines.append(line)
            if task is not None:
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"✅ {_short(task.title)}",
                            callback_data=f"mytask_done:{task.id}",
                        )
                    ]
                )

        text = "\n".join(lines) if lines else "You have no open tasks."
        keyboard = InlineKeyboardMarkup(rows) if rows else None
        return text, keyboard


async def my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return

    if chat.type != ChatType.PRIVATE:
        await message.reply_text(
            "Send /mytasks to me privately to see tasks across all your groups."
        )
        return

    text, keyboard = build_my_tasks(user.id)
    await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def handle_mytasks_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return

    _, _, raw_id = query.data.partition(":")
    try:
        task_id = int(raw_id)
    except ValueError:
        await query.answer()
        return

    user = query.from_user
    now = now_utc()
    with SessionLocal() as session:
        task = session.get(Task, task_id)
        if task is None or task.status != "open":
            await query.answer("This task is no longer open.")
            return
        owner = session.get(User, task.owner_user_id) if task.owner_user_id else None
        if owner is None or owner.telegram_user_id != user.id:
            await query.answer("Only the owner can complete this.", show_alert=True)
            return
        task.status = "done"
        task.completed_at = now
        session.commit()

    text, keyboard = build_my_tasks(user.id)
    try:
        await query.edit_message_text(
            text, reply_markup=keyboard, parse_mode=ParseMode.HTML
        )
    except Exception:
        logger.exception("Failed to refresh /mytasks")
    await query.answer()
