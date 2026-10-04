import logging

from typing import Optional

from app.domains.knowledge.schema import (
    RetrievalResult,
)
from app.domains.knowledge.vector_repository import (
    QdrantVectorRepository,
)
from app.infra.embedding.port import EmbeddingPort
from app.infra.reranking.port import (
    RerankCandidate,
    RerankerPort,
    RerankerRequestError,
    RerankerUnavailableError,
)


logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(
        self,
        embedding: EmbeddingPort,
        vector_repo: QdrantVectorRepository,
        reranker: Optional[RerankerPort],
        rerank_candidate_limit: int,
    ):
        self.embedding = embedding
        self.vector_repo = vector_repo
        self.reranker = reranker
        self.rerank_candidate_limit = rerank_candidate_limit


    async def search(
        self,
        workspace_id: int,
        query: str,
        limit: int = 10,
    ) -> list[RetrievalResult]:
        query = query.strip()

        if not query:
            return []

        dense_vector = await self.embedding.embed_query(query)

        candidate_limit = limit

        if self.reranker is not None:
            candidate_limit = max(
                limit,
                self.rerank_candidate_limit,
            )

        candidates = await self.vector_repo.hybrid_search(
            workspace_id=workspace_id,
            query=query,
            dense_vector=dense_vector,
            limit=candidate_limit,
        )

        if not candidates:
            return []

        if self.reranker is None:
            return candidates[:limit]

        return await self._rerank(
            query=query,
            candidates=candidates,
            limit=limit,
        )


    async def _rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        limit: int,
    ) -> list[RetrievalResult]:
        reranker = self.reranker

        if reranker is None:
            return candidates[:limit]

        rerank_candidates = [
            RerankCandidate(
                chunk_id=candidate.chunk_id,
                text=self._format_candidate(candidate),
            )
            for candidate in candidates
        ]

        try:
            reranked = await reranker.rerank(
                query=query,
                candidates=rerank_candidates,
                limit=limit,
            )

        except RerankerUnavailableError:
            logger.warning(
                "Reranker unavailable, using RRF results",
                exc_info=True,
            )
            return candidates[:limit]

        except RerankerRequestError:
            logger.error(
                "Reranker request failed, using RRF results",
                exc_info=True,
            )
            return candidates[:limit]

        candidates_by_chunk_id = {
            candidate.chunk_id: candidate
            for candidate in candidates
        }

        results = [
            candidates_by_chunk_id[
                rerank_result.chunk_id
            ].model_copy(
                update={
                    "rerank_score": rerank_result.score
                }
            )
            for rerank_result in reranked
        ]

        if not results:
            return candidates[:limit]

        return results


    @staticmethod
    def _format_candidate(
            candidate: RetrievalResult,
    ) -> str:
        values = [
            f"title: {candidate.title}",
        ]

        if candidate.section:
            values.append(
                f"section: {candidate.section}"
            )

        if candidate.page_number is not None:
            values.append(
                f"page: {candidate.page_number}"
            )

        values.append(
            f"content: {candidate.content}"
        )

        return "\n".join(values)