import logging

from typing import Optional

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.knowledge.schema import (
    KnowledgeReadResult,
    RetrievalResult,
)
from app.domains.knowledge.vector_repository import (
    QdrantVectorRepository,
)
from app.domains.knowledge.document.repository import (
    DocumentRepository,
    DocumentChunkRepository,
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
        session_factory: async_sessionmaker[AsyncSession],
        reranker: Optional[RerankerPort],
        rerank_candidate_limit: int,
    ):
        self.embedding = embedding
        self.vector_repo = vector_repo
        self.reranker = reranker
        self.rerank_candidate_limit = rerank_candidate_limit
        self.session_factory = session_factory


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

        searchable_ids = (
            await self._get_searchable_version_ids(
                workspace_id=workspace_id,
                version_ids=[
                    candidate.document_version_id
                    for candidate in candidates
                ],
            )
        )

        candidates = [
            candidate
            for candidate in candidates
            if candidate.document_version_id in searchable_ids
        ]

        if not candidates:
            return []

        if self.reranker is None:
            return candidates[:limit]

        return await self._rerank(
            query=query,
            candidates=candidates,
            limit=limit,
        )


    async def _get_searchable_version_ids(
        self,
        workspace_id: int,
        version_ids: list[int],
    ) -> set[int]:
        if not version_ids:
            return set()

        async with self.session_factory() as session:
            repository = DocumentRepository(session)
            return await repository.get_searchable_version_ids(
                workspace_id=workspace_id,
                version_ids=version_ids,
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


    async def read_document(
        self,
        *,
        workspace_id: int,
        document_version_id: int,
        start_chunk_index: int = 0,
        limit: int = 5,
    ) -> KnowledgeReadResult | None:
        if start_chunk_index < 0 or not 1 <= limit <= 10:
            raise ValueError(
                "Invalid document read range"
            )

        async with self.session_factory() as session:
            repository = DocumentChunkRepository(session)

            chunks = await repository.get_readable_range(
                workspace_id=workspace_id,
                document_version_id=document_version_id,
                start_chunk_index=start_chunk_index,
                limit=limit + 1,
            )

        if not chunks:
            return None

        has_more = len(chunks) > limit

        return KnowledgeReadResult(
            chunks=chunks[:limit],
            has_more=has_more,
            next_chunk_index=(
                chunks[limit].chunk_index
                if has_more
                else None
            ),
        )


    async def read_adjacent_chunks(
        self,
        *,
        workspace_id: int,
        chunk_id: int,
        before: int = 1,
        after: int = 1,
    ) -> KnowledgeReadResult | None:
        if not 0 <= before <= 3 or not 0 <= after <= 3:
            raise ValueError(
                "Invalid adjacent chunk range"
            )

        async with self.session_factory() as session:
            repository = DocumentChunkRepository(session)

            anchor = await repository.get_readable_chunk(
                workspace_id=workspace_id,
                chunk_id=chunk_id,
            )

            if anchor is None:
                return None

            if anchor.chunk_index is None:
                raise RuntimeError(
                    "The anchor chunk has no chunk index"
                )

            chunks = await repository.get_readable_range(
                workspace_id=workspace_id,
                document_version_id=(
                    anchor.document_version_id
                ),
                start_chunk_index=max(
                    0,
                    anchor.chunk_index - before,
                ),
                end_chunk_index=(
                    anchor.chunk_index + after
                ),
                limit=before + after + 1,
            )

        if not chunks:
            return None

        return KnowledgeReadResult(chunks=chunks)