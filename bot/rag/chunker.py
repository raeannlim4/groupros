from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from telegram.ext import ContextTypes

from bot.db import SessionLocal
from bot.embeddings import embed_texts
from bot.models import Message, MessageChunk, User
from bot.timeutil import format_compact, now_utc

logger = logging.getLogger(__name__)

CHUNK_SIZE = 8
PARTIAL_AGE = timedelta(minutes=10)
EMBED_BATCH = 64
CONTEXT_MESSAGES = 2


def _name_for(session, cache: dict, user_id: int) -> str:
    if user_id not in cache:
        user = session.get(User, user_id)
        cache[user_id] = (
            (user.first_name or user.username or "Unknown") if user else "Unknown"
        )
    return cache[user_id]


def _line(session, cache: dict, message: Message, context: bool = False) -> str:
    stamp = format_compact(message.sent_at)
    name = _name_for(session, cache, message.user_id)
    suffix = " (context)" if context else ""
    return f"[{message.telegram_message_id}] {stamp} {name}: {message.text}{suffix}"


def _build_chunk_specs(session, chat_id: int, now) -> list[list[Message]]:
    messages = list(
        session.scalars(
            select(Message)
            .where(Message.chat_id == chat_id, Message.chunked.is_(False))
            .order_by(Message.id)
        )
    )
    specs: list[list[Message]] = []
    for start in range(0, len(messages), CHUNK_SIZE):
        batch = messages[start : start + CHUNK_SIZE]
        if len(batch) < CHUNK_SIZE and batch[-1].sent_at > now - PARTIAL_AGE:
            break
        specs.append(batch)
    return specs


def _build_text(session, cache: dict, chat_id: int, batch: list[Message]) -> str:
    earlier = list(
        session.scalars(
            select(Message)
            .where(Message.chat_id == chat_id, Message.id < batch[0].id)
            .order_by(Message.id.desc())
            .limit(CONTEXT_MESSAGES)
        )
    )
    lines = [_line(session, cache, m, context=True) for m in reversed(earlier)]
    lines.extend(_line(session, cache, m) for m in batch)
    return "\n".join(lines)


async def _embed_all(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        vectors.extend(await embed_texts(texts[start : start + EMBED_BATCH]))
    return vectors


async def _process_chat(session, chat_id: int, now) -> None:
    specs = _build_chunk_specs(session, chat_id, now)
    if not specs:
        return

    cache: dict = {}
    texts = [_build_text(session, cache, chat_id, batch) for batch in specs]
    vectors = await _embed_all(texts)

    for batch, text, vector in zip(specs, texts, vectors):
        session.add(
            MessageChunk(
                chat_id=chat_id,
                first_message_id=batch[0].id,
                last_message_id=batch[-1].id,
                text=text,
                embedding=vector,
            )
        )
        for message in batch:
            message.chunked = True
    session.commit()
    logger.info("Chunked %d chunks for chat %s.", len(specs), chat_id)


async def chunk_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = now_utc()
    with SessionLocal() as session:
        chat_ids = list(
            session.scalars(
                select(Message.chat_id)
                .where(Message.chunked.is_(False))
                .distinct()
            )
        )
    for chat_id in chat_ids:
        try:
            with SessionLocal() as session:
                await _process_chat(session, chat_id, now)
        except Exception:
            logger.exception("Chunking failed for chat %s", chat_id)
