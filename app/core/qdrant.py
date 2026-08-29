from qdrant_client import AsyncQdrantClient, models

from app.core.config import get_settings


settings = get_settings()

_client: AsyncQdrantClient | None = None


async def init_qdrant() -> None:
    global _client

    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
        )


def get_qdrant() -> AsyncQdrantClient:
    if _client is None:
        raise RuntimeError("Qdrant is not initialized")

    return _client


async def ensure_collection() -> None:
    client = get_qdrant()

    exists = await client.collection_exists(
        collection_name=settings.QDRANT_COLLECTION,
    )

    if exists:
        return

    await client.create_collection(
        collection_name=settings.QDRANT_COLLECTION,
        vectors_config=models.VectorParams(
            size=settings.EMBEDDING_DIMENSION,
            distance=models.Distance.COSINE,
        ),
    )


async def qdrant_ping() -> bool:
    try:
        await get_qdrant().get_collections()
        return True
    except Exception:
        return False


async def close_qdrant() -> None:
    global _client

    if _client is not None:
        await _client.close()
        _client = None