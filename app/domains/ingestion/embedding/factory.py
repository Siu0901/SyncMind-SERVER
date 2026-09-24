from openai import AsyncOpenAI

from app.core.config import get_settings
from app.domains.ingestion.embedding.adapter.openai_embedding import (
    OpenAIEmbeddingAdapter,
)
from app.domains.ingestion.embedding.port import EmbeddingPort


settings = get_settings()


def create_embedding() -> EmbeddingPort:
    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY.get_secret_value()
    )

    if settings.EMBEDDING_PROVIDER == "openai":
        return OpenAIEmbeddingAdapter(client=client)

    return OpenAIEmbeddingAdapter(client=client)