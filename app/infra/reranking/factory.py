from typing import Optional

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.config import get_settings
from app.infra.reranking.adapter.cohere_adapter import (
    cohere_reranker_context,
)
from app.infra.reranking.port import RerankerPort


settings = get_settings()


_reranker: Optional[RerankerPort] = None


# 일단 cohere 이놈은 따로 클라 close 이런거 없고 자체 컨텍스트 메니저 있어서, 이렇게 해줘야됨.
@asynccontextmanager
async def reranker_lifespan(
) -> AsyncIterator[Optional[RerankerPort]]:
    global _reranker

    if not settings.RERANK_ENABLED:
        yield None
        return

    if settings.RERANK_PROVIDER == "cohere":
        async with cohere_reranker_context(
            api_key=settings.COHERE_API_KEY.get_secret_value(),
            model=settings.RERANK_MODEL,
            timeout=settings.RERANK_TIMEOUT_SECONDS,
            max_retries=settings.RERANK_MAX_RETRIES,
        ) as reranker:
            _reranker = reranker

            try:
                yield _reranker
            finally:
                _reranker = None

        return

    raise RuntimeError(
        "Unsupported reranker provider: "
        f"{settings.RERANK_PROVIDER}"
    )


def get_reranker() -> Optional[RerankerPort]:
    if (
        settings.RERANK_ENABLED
        and _reranker is None
    ):
        raise RuntimeError(
            "Reranker is not initialized"
        )

    return _reranker