from __future__ import annotations

import logging

from openai import AsyncOpenAI

from bot import config

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    base_url=config.EMBEDDING_BASE_URL,
    api_key=config.EMBEDDING_API_KEY,
    timeout=60.0,
)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts, returning vectors in the same order."""
    if not texts:
        return []
    response = await _client.embeddings.create(
        model=config.EMBEDDING_MODEL,
        input=texts,
    )
    data = sorted(response.data, key=lambda item: item.index)
    return [item.embedding for item in data]
