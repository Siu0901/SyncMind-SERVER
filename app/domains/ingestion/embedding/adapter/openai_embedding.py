from openai import AsyncOpenAI

from app.core.config import get_settings
from app.domains.ingestion.embedding.port import EmbeddingPort


settings = get_settings()


class OpenAIEmbeddingAdapter:
    def __init__(self, client: AsyncOpenAI):
        self.client = client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        embeddings: list[list[float]] = []

        for start in range(
            0,
            len(texts),
            settings.OPENAI_EMBED_BATCH_SIZE
        ):
            batch = texts[
                start: start +
                settings.OPENAI_EMBED_BATCH_SIZE
            ]

            response = await self.client.embeddings.create(
                model="text-embedding-3-large",
                input=batch,
                dimensions=settings.EMBEDDING_DIMENSION,
            )

            embeddings.extend(
                dim.embedding
                for dim in response.data
            )

        return embeddings