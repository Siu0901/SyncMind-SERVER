from app.infra.embedding.port import EmbeddingPort
from app.domains.knowledge.vector_repository import QdrantVectorRepository
from app.domains.knowledge.schema import RetrievalResult


class RetrievalService:
    def __init__(
        self,
        embedding: EmbeddingPort,
        vector_repo: QdrantVectorRepository,
    ):
        self.embedding = embedding
        self.vector_repo = vector_repo


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

        return await self.vector_repo.hybrid_search(
            workspace_id=workspace_id,
            query=query,
            dense_vector=dense_vector,
            limit=limit,
        )
