from __future__ import annotations

from sqlalchemy import select

from bot.db import SessionLocal
from bot.models import MessageChunk

DEFAULT_LIMIT = 6


async def search_chunks(
    chat_id: int, query_embedding: list[float], limit: int = DEFAULT_LIMIT
) -> list[tuple[MessageChunk, float]]:
    """Return (chunk, cosine distance) for one chat, closest first."""
    distance = MessageChunk.embedding.cosine_distance(query_embedding)
    with SessionLocal() as session:
        rows = session.execute(
            select(MessageChunk, distance.label("distance"))
            .where(MessageChunk.chat_id == chat_id)
            .order_by(distance)
            .limit(limit)
        ).all()
        return [(row[0], float(row[1])) for row in rows]


async def retrieve_chunks(
    chat_id: int, query_embedding: list[float], limit: int = DEFAULT_LIMIT
) -> list[MessageChunk]:
    return [chunk for chunk, _ in await search_chunks(chat_id, query_embedding, limit)]
