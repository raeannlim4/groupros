"""``/plan`` — turn the brief's deadline into claimable milestones."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from bot.brief import KEY_FIELDS, BriefFacts, has_value
from bot.db import SessionLocal, get_or_create_chat, get_or_create_user
from bot.llm import PlanMilestone, parse_task, plan_milestones
from bot.models import ProjectBrief, Task
from bot.timeutil import format_absolute, now_utc, to_local

logger = logging.getLogger(__name__)

USAGE = (
    "Usage: /plan  (uses the brief's final deadline)  or  /plan by <date>\n\n"
    "Upload the assignment brief first with /brief so I know the deadline."
)
NO_DEADLINE = (
    "I need a calendar deadline to plan. Upload the brief with /brief, or use "
    "/plan by <date>."
)
NO_PLAN = "Sorry, I couldn't build a plan from the brief. Try /plan by <date>."
PRIVATE = "Use /plan in a group chat."
FAILED = "Sorry, something went wrong, try again."


@dataclass
class _Plan:
    chat_db_id: int
    milestones: list[PlanMilestone]


_plans: dict[tuple[int, int], _Plan] = {}


def _brief_text(facts: BriefFacts) -> str:
    lines: list[str] = []
    for key, label in KEY_FIELDS:
        fact = getattr(facts, key)
        if has_value(fact):
            lines.append(f"{label}: {fact.value}")
    for deliverable in facts.deliverables:
        if deliverable.value.strip():
            lines.append(f"Deliverable: {deliverable.value}")
    for other in facts.other_key_dates:
        if other.value.strip():
            lines.append(f"Key date: {other.value}")
    return "\n".join(lines)


def _existing_titles(chat_db_id: int) -> list[str]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(Task.title).where(
                    Task.chat_id == chat_db_id, Task.status == "open"
                )
            )
        )


def _render_plan(milestones: list[PlanMilestone], deadline) -> str:
    lines = [f"🗺 Plan to {format_absolute(deadline)}"]
    for milestone in milestones:
        due = f" — due {format_absolute(milestone.due)}" if milestone.due else ""
        lines.append(f"• {html.escape(milestone.title)} {due}".rstrip())
    lines.append("")
    lines.append("Create them as unclaimed tasks the group can claim?")
    return "\n".join(lines)


def filter_milestones(
    milestones: list[PlanMilestone],
    start_date,
    end_date,
    existing: list[str],
) -> list[PlanMilestone]:
    """Keep milestones within [start_date, end_date], dropping duplicates."""
    seen = {title.strip().lower() for title in existing}
    valid: list[PlanMilestone] = []
    for milestone in milestones:
        title = milestone.title.strip()
        if not title or milestone.due is None:
            continue
        day = to_local(milestone.due).date()
        if day < start_date or day > end_date:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        valid.append(milestone)
    return valid


async def plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    if chat.type == ChatType.PRIVATE:
        await message.reply_text(PRIVATE)
        return

    now = now_utc()
    args = " ".join(context.args or []).strip()

    deadline = None
    if args:
        if args.lower().startswith("by "):
            value = args[3:].strip()
        else:
            await message.reply_text(USAGE)
            return
        if not value:
            await message.reply_text(USAGE)
            return
        try:
            parsed = await parse_task(value, now)
        except Exception:
            logger.exception("plan failed to parse deadline for chat %s", chat.id)
            await message.reply_text(FAILED)
            return
        deadline = parsed.due if parsed is not None else None
        if deadline is None:
            await message.reply_text(USAGE)
            return

    with SessionLocal() as session:
        db_chat = get_or_create_chat(session, chat.id, chat.title)
        get_or_create_user(session, user.id, user.username, user.first_name)
        session.commit()
        chat_db_id = db_chat.id
        brief = session.scalar(
            select(ProjectBrief).where(ProjectBrief.chat_id == chat_db_id)
        )
        facts = (
            BriefFacts.model_validate(brief.facts or {})
            if brief is not None
            else None
        )

    brief_text = _brief_text(facts) if facts is not None else ""
    if deadline is None and facts is not None and has_value(facts.final_deadline):
        deadline = facts.final_deadline.date
    if deadline is None:
        await message.reply_text(NO_DEADLINE)
        return

    existing = _existing_titles(chat_db_id)
    try:
        milestones = await plan_milestones(deadline, brief_text, existing, now)
    except Exception:
        logger.exception("plan generation failed for chat %s", chat.id)
        await message.reply_text(FAILED)
        return

    milestones = filter_milestones(
        milestones, to_local(now).date(), to_local(deadline).date(), existing
    )
    if not milestones:
        await message.reply_text(NO_PLAN)
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Create as unclaimed tasks", callback_data="plan_create:0"
                ),
                InlineKeyboardButton("Cancel", callback_data="plan_cancel:0"),
            ]
        ]
    )
    sent = await message.reply_text(_render_plan(milestones, deadline), reply_markup=keyboard)
    _plans[(chat.id, sent.message_id)] = _Plan(chat_db_id, milestones)


async def handle_plan_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or query.data is None or query.message is None:
        return
    action = query.data.split(":")[0]
    key = (query.message.chat_id, query.message.message_id)
    pending = _plans.pop(key, None)
    if pending is None:
        await query.answer("This plan has expired. Run /plan again.", show_alert=True)
        return

    if action == "plan_cancel":
        await query.edit_message_text("Plan dismissed")
        await query.answer()
        return

    with SessionLocal() as session:
        for milestone in pending.milestones:
            session.add(
                Task(
                    chat_id=pending.chat_db_id,
                    owner_user_id=None,
                    title=milestone.title.strip(),
                    due_at=milestone.due,
                    status="open",
                    source="plan",
                )
            )
        session.commit()
        count = len(pending.milestones)
    await query.edit_message_text(f"✅ Added {count} tasks — claim them on /tasks")
    await query.answer()
