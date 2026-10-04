from openai import AsyncOpenAI, OpenAIError

from app.core.config import get_settings
from app.infra.embedding.port import EmbeddingPort


settings = get_settings()


class OpenAIEmbeddingAdapter(EmbeddingPort):
    def __init__(self, client: AsyncOpenAI):
        self.client = client


    async def embed_document(self, texts: list[str]) -> list[list[float]]:
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

            embeddings.extend(
                await self._embed(batch)
            )

        return embeddings


    async def embed_query(self, texts: str) -> list[float]:
        vectors = await self._embed([texts])
        return vectors[0]


    async def close(self):
        await self.client.close()


    async def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self.client.embeddings.create(
                model=settings.OPENAI_EMBED_MODEL,
                input=texts,
                dimensions=settings.EMBEDDING_DIMENSION,
            )

        except OpenAIError:
            raise OpenAIError()

        vectors = [
            list(vector.embedding)
            for vector in response.data
        ]

        return vectors