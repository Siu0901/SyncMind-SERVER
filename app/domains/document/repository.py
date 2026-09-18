from typing import Optional

from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.document.model import (
    Document,
    DocumentChunk,
    DocumentVersion,
)


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(
        self,
        workspace_id: int,
        document_id: int,
    ) -> Optional[Document]:
        statement = select(Document).where(
            Document.id == document_id,
            Document.workspace_id == workspace_id,
        )

        result = await self.session.exec(statement)

        return result.first()


    async def get_raw_by_id(
        self,
        document_id: int,
    ) -> Optional[Document]:
        statement = select(Document).where(Document.id == document_id)
        return (await self.session.exec(statement)).first()



    async def get_all(self, workspace_id: int) -> list[Document]:
        statement = (select(Document)
            .where(
                Document.workspace_id
                == workspace_id
            )
            .order_by(desc(Document.updated_at))
        )

        result = await self.session.exec(statement)

        return list(result.all())

    async def get_by_source_external(
        self,
        source_id: int,
        external_id: str,
    ) -> Optional[Document]:
        statement = select(Document).where(
            Document.source_id == source_id,
            Document.external_id == external_id,
        )

        result = await self.session.exec(statement)

        return result.first()

    async def create(self, document: Document) -> Document:
        self.session.add(document)

        await self.session.flush()
        await self.session.refresh(document)

        return document

    async def delete(self, document: Document):
        await self.session.delete(document)


class DocumentVersionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, version_id: int) -> Optional[DocumentVersion]:
        statement = select(DocumentVersion).where(DocumentVersion.id == version_id)
        result = await self.session.exec(statement)

        return result.first()

    async def get_all(self, document_id: int) -> list[DocumentVersion]:
        statement = (select(DocumentVersion)
            .where(
                DocumentVersion.document_id
                == document_id
            )
            .order_by(desc(DocumentVersion.version))
        )

        result = await self.session.exec(statement)

        return list(result.all())

    async def get_active(self, document_id: int) -> Optional[DocumentVersion]:
        statement = (select(DocumentVersion)
            .where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.is_active == True,
            )
        )

        result = await self.session.exec(statement)

        return result.first()

    async def get_latest(self, document_id: int) -> Optional[DocumentVersion]:
        statement = (select(DocumentVersion)
            .where(
                DocumentVersion.document_id
                == document_id
            )
            .order_by(desc(DocumentVersion.version))
            .limit(1)
        )

        result = await self.session.exec(statement)

        return result.first()

    async def exists_by_content_hash(
            self,
            content_hash: str,
            workspace_id: int,
    ) -> bool:
        statement = (select(DocumentVersion.id)
            .join(Document, DocumentVersion.id == Document.id)
            .where(
            Document.workspace_id == workspace_id,
                    DocumentVersion.content_hash == content_hash
            )
            .limit(1)
        )
        result = await self.session.exec(statement)
        return result.first() is not None

    async def create(self, version: DocumentVersion) -> DocumentVersion:
        self.session.add(version)

        await self.session.flush()
        await self.session.refresh(version)

        return version


class DocumentChunkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_by_version(self, version_id: int) -> list[DocumentChunk]:
        statement = (select(DocumentChunk)
            .where(
                DocumentChunk.document_version_id
                == version_id
            )
            .order_by(DocumentChunk.chunk_index)
        )

        result = await self.session.exec(statement)

        return list(result.all())

    async def create_many(self, chunks: list[DocumentChunk]):
        self.session.add_all(chunks)

        await self.session.flush()