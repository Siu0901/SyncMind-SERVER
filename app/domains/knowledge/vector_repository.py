from qdrant_client import AsyncQdrantClient, models

from app.domains.knowledge.document.model import (
    Document,
    DocumentChunk,
    DocumentVersion
)

from app.domains.knowledge.schema import RetrievalResult

from app.core.config import get_settings


settings = get_settings()


class QdrantVectorRepository:
    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    async def upsert_chunks(
        self,
        document: Document,
        version: DocumentVersion,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ):
        points = [
            models.PointStruct(
                id=chunk.id,
                vector={
                    "dense": embedding,
                    "bm25": models.Document(
                        text=chunk.content,
                        model="qdrant/bm25",
                    ),
                },
                payload={
                    "workspace_id": document.workspace_id,
                    "document_id": document.id,
                    "document_version_id": version.id,
                    "chunk_id": chunk.id,
                    "chunk_index": chunk.chunk_index,
                    "title": document.title,
                    "content": chunk.content,
                    "page_number": chunk.page_number,
                    "section": chunk.section,
                },
            )
            for chunk, embedding in zip(
                chunks,
                embeddings,
                strict=True,
            )
        ]

        await self.client.upsert(
            collection_name=settings.QDRANT_COLLECTION,
            points=points,
            wait=True,
        )


    async def delete_by_document(self, document_id: int):
        await self.client.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(
                                value=document_id
                            ),
                        )
                    ]
                )
            ),
            wait=True,
        )


    async def delete_by_version(self, document_version_id: int):
        await self.client.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_version_id",
                            match=models.MatchValue(
                                value=document_version_id
                            ),
                        )
                    ]
                )
            ),
            wait=True,
        )


    async def hybrid_search(
        self,
        workspace_id: int,
        query: str,
        dense_vector: list[float],
        limit: int,
    ) -> list[RetrievalResult]:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="workspace_id",
                    match=models.MatchValue(value=workspace_id)
                )
            ]
        )

        prefetch_limit = max(
            20,
            limit,
        )

        response = await self.client.query_points(
            collection_name=settings.QDRANT_COLLECTION,

            prefetch=[
                models.Prefetch(
                    query=dense_vector,
                    using="dense",
                    filter=query_filter,
                    limit=prefetch_limit,
                ),

                models.Prefetch(
                    query=models.Document(
                        text=query,
                        model="qdrant/bm25",
                    ),
                    using="bm25",
                    filter=query_filter,
                    limit=prefetch_limit,
                ),
            ],
            query=models.FusionQuery(
                fusion=models.Fusion.RRF,
            ),
            limit=limit,
            with_payload=True,
        )

        results: list[RetrievalResult] = []

        for point in response.points:
            payload = point.payload or {}

            results.append(
                RetrievalResult(
                    chunk_id=payload["chunk_id"],
                    document_id=payload["document_id"],
                    document_version_id=payload[
                        "document_version_id"
                    ],
                    title=payload["title"],
                    content=payload["content"],
                    page_number=payload.get(
                        "page_number"
                    ),
                    section=payload.get(
                        "section"
                    ),
                    retrieval_score=point.score,
                    rerank_score=None,
                )
            )

        return results
