from typing import Optional

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.infra.embedding.adapter.openai_embedding import (
    OpenAIEmbeddingAdapter,
)
from app.infra.embedding.port import EmbeddingPort


settings = get_settings()


_embedding: Optional[EmbeddingPort] = None


def create_embedding() -> EmbeddingPort:
    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY.get_secret_value()
    )

    if settings.EMBEDDING_PROVIDER == "openai":
        return OpenAIEmbeddingAdapter(client=client)

    return OpenAIEmbeddingAdapter(client=client)


def init_embedding() -> EmbeddingPort:
    global _embedding

    if _embedding is None:
        _embedding = create_embedding()

    return _embedding


def get_embedding() -> EmbeddingPort:
    if _embedding is None:
        raise RuntimeError("Embedding is not initialized")

    return _embedding


async def close_embedding():
    global _embedding

    if _embedding is not None:
        await _embedding.close()
        _embedding = None
