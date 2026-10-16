"""``/brief`` — read an assignment brief and save the key facts."""

from __future__ import annotations

import html
import logging

from sqlalchemy import select
from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from bot import documents
from bot.brief import (
    BriefFact,
    BriefFacts,
    ground_facts,
    has_value,
    missing_labels,
)
from bot.db import SessionLocal, get_or_create_chat, get_or_create_user
from bot.llm import (
    BriefTimeoutError,
    BriefTooLongError,
    extract_brief,
    parse_task,
)
from bot.models import ProjectBrief, User
from bot.timeutil import format_absolute, now_utc

logger = logging.getLogger(__name__)

USAGE = (
    "Send me the assignment brief and I'll save its key facts.\n\n"
    "Attach a .pdf, .docx or .txt file with the caption /brief, reply /brief to "
    "a document or a text message, or send /brief followed by the text.\n\n"
    "See the saved brief with /brief, or override a date with "
    "/brief set deadline <date> or /brief set presentation <date>."
)
SET_USAGE = "Usage: /brief set deadline <date>  or  /brief set presentation <date>"
PRIVATE = "Use /brief in a group chat."
FAILED = "Sorry, something went wrong, try again."
TIMEOUT = "Reading the brief took too long. Try again, or send a smaller file."
SET_FIELDS = {
    "deadline": "final_deadline",
    "final_deadline": "final_deadline",
    "presentation": "presentation",
}


def _name(user: User | None) -> str:
    if user is None:
        return "someone"
    return user.first_name or user.username or "someone"


def _fact_html(fact: BriefFact) -> str:
    value = html.escape(fact.value)
    if fact.date:
        return f"{value} ({format_absolute(fact.date)})"
    return value


def _deadline_html(fact: BriefFact) -> str:
    if fact.date:
        return format_absolute(fact.date)
    return html.escape(fact.value)


def render_facts(facts: BriefFacts, filename: str | None, uploader_name: str) -> str:
    source = html.escape(filename) if filename else "pasted text"
    lines = [f"📋 Assignment brief ({source})"]
    if has_value(facts.final_deadline):
        lines.append(f"📅 Final deadline: {_deadline_html(facts.final_deadline)}")
    if has_value(facts.presentation):
        lines.append(f"🎤 Presentation: {_fact_html(facts.presentation)}")
    deliverables = [d for d in facts.deliverables if d.value.strip()]
    if deliverables:
        lines.append("📦 Deliverables:")
        lines.extend(f"• {_fact_html(d)}" for d in deliverables)
    if has_value(facts.weighting):
        lines.append(f"⚖️ Weighting: {_fact_html(facts.weighting)}")
    if has_value(facts.word_limit):
        lines.append(f"📝 Word limit: {_fact_html(facts.word_limit)}")
    if has_value(facts.group_size):
        lines.append(f"👥 Group size: {_fact_html(facts.group_size)}")
    other = [d for d in facts.other_key_dates if d.value.strip()]
    if other:
        lines.append("🗓 Other key dates:")
        lines.extend(f"• {_fact_html(d)}" for d in other)
    missing = missing_labels(facts)
    if missing:
        lines.append("")
        lines.append("❓ Not stated in brief: " + ", ".join(missing))
    return "\n".join(lines)


async def _read_source(update: Update, context: ContextTypes.DEFAULT_TYPE, args: str):
    """Return (text, source_date, filename), or None when there is no input."""
    message = update.effective_message
    document = message.document
    source_date = message.date
    if document is None and message.reply_to_message is not None:
        reply = message.reply_to_message
        document = reply.document
        if document is not None:
            source_date = reply.date
        elif reply.text:
            return documents.check_text(reply.text), reply.date, None
    if document is not None:
        name = document.file_name or ""
        if document.file_size and document.file_size > documents.MAX_FILE_BYTES:
            raise documents.DocumentError(documents.too_long(document.file_size))
        tg_file = await context.bot.get_file(document.file_id)
        data = await tg_file.download_as_bytearray()
        text = documents.extract_text(name, bytes(data))
        return text, source_date, name
    if args:
        return documents.check_text(args), message.date, None
    return None


def _upsert_brief(
    chat_db_id: int, facts: BriefFacts, filename: str | None, user_db_id: int | None
) -> None:
    payload = facts.model_dump(mode="json")
    with SessionLocal() as session:
        brief = session.scalar(
            select(ProjectBrief).where(ProjectBrief.chat_id == chat_db_id)
        )
        if brief is None:
            brief = ProjectBrief(chat_id=chat_db_id, facts=payload)
            session.add(brief)
        else:
            brief.facts = payload
        brief.source_filename = filename
        brief.uploaded_by_user_id = user_db_id
        session.commit()


async def _show_saved(message, chat_db_id: int) -> None:
    with SessionLocal() as session:
        brief = session.scalar(
            select(ProjectBrief).where(ProjectBrief.chat_id == chat_db_id)
        )
        if brief is None:
            await message.reply_text(USAGE)
            return
        facts = BriefFacts.model_validate(brief.facts or {})
        filename = brief.source_filename
        uploader = (
            session.get(User, brief.uploaded_by_user_id)
            if brief.uploaded_by_user_id
            else None
        )
        uploader_name = _name(uploader)
    await message.reply_text(
        render_facts(facts, filename, uploader_name), parse_mode=ParseMode.HTML
    )


async def _handle_set(update: Update, context: ContextTypes.DEFAULT_TYPE, args: str) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    tokens = args.split(maxsplit=2)
    if len(tokens) < 3 or not tokens[2].strip():
        await message.reply_text(SET_USAGE)
        return
    key = SET_FIELDS.get(tokens[1].lower())
    if key is None:
        await message.reply_text(SET_USAGE)
        return
    value = tokens[2].strip()

    try:
        parsed = await parse_task(value, now_utc())
    except Exception:
        logger.exception("brief set failed to parse date for chat %s", chat.id)
        await message.reply_text(FAILED)
        return
    due = parsed.due if parsed is not None else None

    with SessionLocal() as session:
        db_chat = get_or_create_chat(session, chat.id, chat.title)
        db_user = get_or_create_user(
            session, user.id, user.username, user.first_name
        )
        brief = session.scalar(
            select(ProjectBrief).where(ProjectBrief.chat_id == db_chat.id)
        )
        facts = (
            BriefFacts.model_validate(brief.facts or {})
            if brief is not None
            else BriefFacts()
        )
        setattr(facts, key, BriefFact(value=value, date=due))
        payload = facts.model_dump(mode="json")
        if brief is None:
            brief = ProjectBrief(
                chat_id=db_chat.id, facts=payload, uploaded_by_user_id=db_user.id
            )
            session.add(brief)
        else:
            brief.facts = payload
            brief.uploaded_by_user_id = db_user.id
        session.commit()

    label = "deadline" if key == "final_deadline" else "presentation"
    shown = format_absolute(due) if due else value
    name = user.first_name or user.username or "Someone"
    await message.reply_text(f"✅ Updated {label} to {shown} — by {name}.")


async def brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    if chat.type == ChatType.PRIVATE:
        await message.reply_text(PRIVATE)
        return

    args = " ".join(context.args or []).strip()
    if args == "set" or args.startswith("set "):
        await _handle_set(update, context, args)
        return

    with SessionLocal() as session:
        db_chat = get_or_create_chat(session, chat.id, chat.title)
        db_user = get_or_create_user(
            session, user.id, user.username, user.first_name
        )
        session.commit()
        chat_db_id = db_chat.id
        user_db_id = db_user.id

    try:
        source = await _read_source(update, context, args)
    except documents.DocumentError as exc:
        await message.reply_text(exc.message)
        return
    except Exception:
        logger.exception("Failed to read assignment brief")
        await message.reply_text(FAILED)
        return
    if source is None:
        await _show_saved(message, chat_db_id)
        return

    text, source_date, filename = source
    try:
        facts = await extract_brief(text, source_date)
    except BriefTooLongError:
        await message.reply_text(documents.too_long(len(text)))
        return
    except BriefTimeoutError:
        await message.reply_text(TIMEOUT)
        return
    except Exception:
        logger.exception("Brief extraction failed for chat %s", chat.id)
        await message.reply_text(FAILED)
        return

    facts = ground_facts(facts, text)
    _upsert_brief(chat_db_id, facts, filename, user_db_id)
    uploader_name = user.first_name or user.username or "Someone"
    await message.reply_text(
        render_facts(facts, filename, uploader_name), parse_mode=ParseMode.HTML
    )
