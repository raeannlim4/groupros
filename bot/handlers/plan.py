"""``/plan`` — turn the brief's milestones into claimable tasks."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from bot.brief import BriefFacts, BriefMilestone
from bot.db import SessionLocal, get_or_create_chat, get_or_create_user
from bot.llm import PlanMilestone, parse_task, plan_milestones
from bot.models import ProjectBrief, Task
from bot.timeutil import format_absolute, now_utc, to_local

logger = logging.getLogger(__name__)

USAGE = (
    "Usage: /plan  (plans the brief's upcoming milestones)  or  /plan by <date>"
)
NO_DEADLINE = (
    "I need a calendar deadline to plan. Upload the brief with /brief, or use "
    "/plan by <date>."
)
NO_MILESTONES = (
    "Your saved brief doesn't list any deadlines to plan. Send a clearer brief "
    "with /brief, or use /plan by <date>."
)
NO_PLAN = "Sorry, I couldn't build a plan from the brief. Try /plan by <date>."
PRIVATE = "Use /plan in a group chat."
FAILED = "Sorry, something went wrong, try again."


@dataclass
class _Plan:
    chat_db_id: int
    milestones: list[PlanMilestone]


_plans: dict[tuple[int, int], _Plan] = {}


def _existing_titles(chat_db_id: int) -> list[str]:
    with SessionLocal() as session:
        return list(
            session.scalars(
                select(Task.title).where(
                    Task.chat_id == chat_db_id, Task.status == "open"
                )
            )
        )


def _milestone_brief_text(milestone: BriefMilestone) -> str:
    lines = [f"Milestone: {milestone.name.strip() or 'milestone'}"]
    if milestone.due_text.strip():
        lines.append(f"Deadline as written: {milestone.due_text.strip()}")
    for deliverable in milestone.deliverables:
        if deliverable.value.strip():
            lines.append(f"Deliverable: {deliverable.value.strip()}")
    return "\n".join(lines)


def _teaching_weeks_message(facts: BriefFacts) -> str:
    texts = [m.due_text.strip() or m.name.strip() for m in facts.milestones]
    texts = [text for text in texts if text]
    listing = "; ".join(texts) if texts else "no dates"
    return (
        "Your brief gives deadlines as teaching weeks, not dates: "
        f"{listing}. Set one with /brief set deadline <date>, then run /plan again."
    )


def filter_milestones(
    milestones: list[PlanMilestone],
    start_date,
    end_date,
    existing: list[str],
) -> list[PlanMilestone]:
    """Keep tasks within [start_date, end_date], dropping duplicates."""
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


def _render_plan(
    heading: str, groups: list[tuple[str, list[PlanMilestone]]]
) -> str:
    lines = [heading]
    for label, items in groups:
        if label:
            lines.append(f"{html.escape(label)}:")
        for item in items:
            due = f" — due {format_absolute(item.due)}" if item.due else ""
            lines.append(f"• {html.escape(item.title.strip())}{due}")
    lines.append("")
    lines.append("Create them as unclaimed tasks the group can claim?")
    return "\n".join(lines)


async def _send_plan(
    message,
    chat_id: int,
    chat_db_id: int,
    heading: str,
    groups: list[tuple[str, list[PlanMilestone]]],
    skipped: list[str],
) -> None:
    flat = [item for _label, items in groups for item in items]
    body = list(skipped)
    if skipped:
        body.append("")
    body.append(_render_plan(heading, groups))
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
    sent = await message.reply_text("\n".join(body), reply_markup=keyboard)
    _plans[(chat_id, sent.message_id)] = _Plan(chat_db_id, flat)


async def _generate(
    message, chat, chat_db_id: int, now, deadline, brief_text: str
):
    """Generate one plan to a single deadline. Returns groups or None on error."""
    today = to_local(now).date()
    existing = _existing_titles(chat_db_id)
    try:
        items = await plan_milestones(deadline, brief_text, existing, now)
    except Exception:
        logger.exception("plan generation failed for chat %s", chat.id)
        await message.reply_text(FAILED)
        return None
    items = filter_milestones(
        items, today, to_local(deadline).date(), existing
    )
    return items


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

    override = None
    if args:
        if not args.lower().startswith("by "):
            await message.reply_text(USAGE)
            return
        value = args[3:].strip()
        if not value:
            await message.reply_text(USAGE)
            return
        try:
            parsed = await parse_task(value, now)
        except Exception:
            logger.exception("plan failed to parse deadline for chat %s", chat.id)
            await message.reply_text(FAILED)
            return
        override = parsed.due if parsed is not None else None
        if override is None:
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

    if override is not None:
        items = await _generate(
            message, chat, chat_db_id, now, override, "(no brief uploaded)"
        )
        if items is None:
            return
        if not items:
            await message.reply_text(NO_PLAN)
            return
        await _send_plan(
            message,
            chat.id,
            chat_db_id,
            f"🗺 Plan to {format_absolute(override)}",
            [("", items)],
            [],
        )
        return

    if facts is None or not facts.milestones:
        if facts is not None:
            await message.reply_text(NO_MILESTONES)
            return
        await message.reply_text(NO_DEADLINE)
        return

    today = to_local(now).date()
    future = [
        m
        for m in facts.milestones
        if m.due_date and to_local(m.due_date).date() >= today
    ]
    past = [
        m
        for m in facts.milestones
        if m.due_date and to_local(m.due_date).date() < today
    ]

    if not future:
        await message.reply_text(_teaching_weeks_message(facts))
        return

    all_titles = _existing_titles(chat_db_id)
    groups: list[tuple[str, list[PlanMilestone]]] = []
    for milestone in future:
        brief_text = _milestone_brief_text(milestone)
        try:
            items = await plan_milestones(
                milestone.due_date, brief_text, all_titles, now
            )
        except Exception:
            logger.exception("plan generation failed for chat %s", chat.id)
            await message.reply_text(FAILED)
            return
        items = filter_milestones(
            items, today, to_local(milestone.due_date).date(), all_titles
        )
        all_titles.extend(item.title.strip() for item in items)
        if items:
            groups.append((milestone.name.strip() or "Milestone", items))

    if not groups:
        await message.reply_text(NO_PLAN)
        return

    skipped = [
        f"Skipping {m.name.strip() or 'milestone'} milestone — already past"
        for m in past
    ]
    await _send_plan(message, chat.id, chat_db_id, "🗺 Plan", groups, skipped)


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
