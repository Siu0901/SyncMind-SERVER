from qdrant_client import AsyncQdrantClient, models

from app.domains.document.model import Document, DocumentChunk, DocumentVersion

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