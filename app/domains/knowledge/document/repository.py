from typing import Optional

from datetime import datetime, timezone

from sqlmodel import select, desc, delete, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.knowledge.document.model import (
    Document,
    DocumentChunk,
    DocumentVersion,
    DocumentStatus,
)
from app.domains.knowledge.document.exceptions import (
    DocumentVersionNotFoundError,
)
from app.domains.knowledge.ingestion.schema import ChunkData


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
            Document.deleted_at.is_(None),
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
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
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
            Document.deleted_at.is_(None),
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


    async def get_for_update(self, document_id: int) -> Optional[Document]:
        statement = (
            select(Document)
            .where(Document.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

        result = await self.session.exec(statement)
        return result.first()


    async def mark_processing(self, document: Document):
        if document.current_version is None:
            document.status = DocumentStatus.PROCESSING

            self.session.add(document)
            await self.session.flush()


    async def mark_ready(
        self,
        document: Document,
        version: int,
    ):
        document.status = DocumentStatus.READY
        document.current_version = version

        self.session.add(document)
        await self.session.flush()


    async def mark_ingestion_failed(self, document: Document):
        document.status = (
            DocumentStatus.FAILED
            if document.current_version is None
            else DocumentStatus.READY
        )

        self.session.add(document)
        await self.session.flush()


    async def mark_removed(
        self,
        document_id: int,
        failed: bool,
    ):
        document = await self.get_for_update(document_id)

        if document is None:
            return

        document.deleted_at = datetime.now(timezone.utc)

        if failed and document.current_version is None:
            document.status = DocumentStatus.FAILED

        self.session.add(document)
        await self.session.flush()


    async def delete_by_id(self, document_id: int):
        statement = delete(Document).where(
            Document.id == document_id,
        )
        await self.session.exec(statement)

    async def get_searchable_version_ids(
        self,
        workspace_id: int,
        version_ids: list[int],
    ) -> set[int]:
        if not version_ids:
            return set()

        statement = (
            select(DocumentVersion.id)
            .join(
                Document,
                Document.id == DocumentVersion.document_id,
            )
            .where(
                Document.workspace_id == workspace_id,
                Document.deleted_at.is_(None),
                Document.status == DocumentStatus.READY,
                DocumentVersion.is_active.is_(True),
                DocumentVersion.id.in_(version_ids),
            )
        )

        return set(
            (await self.session.exec(statement)).all()
        )


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
        statement = (
            select(DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(
            Document.workspace_id == workspace_id,
                    DocumentVersion.content_hash == content_hash,
                    Document.deleted_at.is_(None),
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

    async def activate(self, version: DocumentVersion):
        statement = (
            update(DocumentVersion)
            .where(
                DocumentVersion.document_id == version.document_id,
                DocumentVersion.id != version.id,
                DocumentVersion.is_active.is_(True),
            )
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        )

        await self.session.exec(statement)

        version.is_active = True

        self.session.add(version)
        await self.session.flush()


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

    async def delete_by_version(self, document_version_id: int):
        statement = (
            delete(DocumentChunk)
            .where(
                DocumentChunk.document_version_id
                == document_version_id
            )
        )

        await self.session.exec(statement)
        await self.session.flush()

    async def get_or_create_for_version(
        self,
        version_id: int,
        chunk_data: list[ChunkData],
    ) -> list[DocumentChunk]:
        statement = (
            select(DocumentVersion)
            .where(DocumentVersion.id == version_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        version = (await self.session.exec(statement)).first()

        if version is None:
            raise DocumentVersionNotFoundError()

        existing = await self.get_all_by_version(version_id)

        if existing:
            return existing

        chunks = [
            DocumentChunk(
                document_version_id=version_id,
                chunk_index=index,
                content=data.content,
                token_count=data.token_count,
                page_number=data.page_number,
                section=data.section,
                chunk_metadata=data.metadata,
            )
            for index, data in enumerate(chunk_data)
        ]

        await self.create_many(chunks)

        return chunks