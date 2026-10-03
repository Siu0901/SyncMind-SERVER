import httpx

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from cohere import AsyncClientV2
from cohere.errors import (
    BadRequestError,
    ForbiddenError,
    GatewayTimeoutError,
    InternalServerError,
    InvalidTokenError,
    NotFoundError,
    ServiceUnavailableError,
    TooManyRequestsError,
    UnauthorizedError,
    UnprocessableEntityError,
)

from app.infra.reranking.port import (
    RerankCandidate,
    RerankerPort,
    RerankerRequestError,
    RerankerUnavailableError,
    RerankResult,
)


class CohereRerankerAdapter(RerankerPort):
    def __init__(
        self,
        client: AsyncClientV2,
        model: str
    ):
        self.client = client
        self.model = model


    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        limit: int,
    ) -> list[RerankResult]:
        if not candidates or limit <= 0:
            return []

        try:
            response = await self.client.rerank(
                model=self.model,
                query=query,
                documents=[
                    candidate.text
                    for candidate in candidates
                ],
                top_n=min(limit, len(candidates)),
            )

        except (
            TooManyRequestsError,
            GatewayTimeoutError,
            InternalServerError,
            ServiceUnavailableError,
            httpx.RequestError,
        ) as exc:
            raise RerankerUnavailableError(
                "Cohere reranker is unavailable"
            ) from exc

        except (
            BadRequestError,
            UnauthorizedError,
            ForbiddenError,
            InvalidTokenError,
            NotFoundError,
            UnprocessableEntityError,
        ) as exc:
            raise RerankerRequestError(
                "Cohere reranker request failed"
            ) from exc

        results: list[RerankResult] = []

        for result in response.results:
            if not 0 <= result.index < len(candidates):
                raise RerankerRequestError(
                    "Cohere returned an invalid index"
                )

            candidate = candidates[result.index]

            results.append(
                RerankResult(
                    chunk_id=candidate.chunk_id,
                    score=float(
                        result.relevance_score
                    ),
                )
            )

        return results


@asynccontextmanager
async def cohere_reranker_context(
    api_key: str,
    model: str,
    timeout: float,
    max_retries: int,
) -> AsyncIterator[CohereRerankerAdapter]:
    async with AsyncClientV2(
        api_key=api_key,
        client_name="syncmind",
        timeout=timeout,
        max_retries=max_retries,
    ) as client:
        yield CohereRerankerAdapter(
            client=client,
            model=model,
        )