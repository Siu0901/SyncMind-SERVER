import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.document.model import (
    Document,
    DocumentStatus,
    DocumentChunk,
    DocumentVersion,
)
from app.domains.document.repository import (
    DocumentChunkRepository,
    DocumentRepository,
    DocumentVersionRepository,
)
from app.domains.document.schema import (
    DocumentCreateData,
    DocumentUpdateRequest,
    DocumentVersionCreateData,
)


logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        documents_repo: DocumentRepository,
        versions_repo: DocumentVersionRepository,
        chunks_repo: DocumentChunkRepository,
    ):
        self.session = session
        self.documents_repo = documents_repo
        self.versions_repo = versions_repo
        self.chunks_repo = chunks_repo


    async def create_document(
        self,
        data: DocumentCreateData,
    ) -> Document:
        document = Document(
            workspace_id=data.workspace_id,
            source_id=data.source_id,
            external_id=data.external_id,
            title=data.title.strip(),
            status=DocumentStatus.PENDING,
            current_version=None,
        )

        return await self.documents_repo.create(document)


    async def create_version(
        self,
        data: DocumentVersionCreateData,
    ) -> DocumentVersion:
        latest = await self.versions_repo.get_latest(data.document_id)

        next_version = (1 if latest is None else latest.version + 1)

        version = DocumentVersion(
            document_id=data.document_id,
            version=next_version,
            content_hash=data.content_hash,
            s3_key=data.s3_key,
            mime_type=data.mime_type,
            file_size=data.file_size,
            source_updated_at=(
                data.source_updated_at
            ),

            # ingestion 끝나기 전에는 절대 active 아님
            is_active=False,
        )

        return await self.versions_repo.create(version)


    async def activate_version(
        self,
        document: Document,
        version: DocumentVersion,
    ):
        current = await self.versions_repo.get_active(document.id)

        if current is not None:
            current.is_active = False

            self.session.add(current)

        version.is_active = True

        document.current_version = version.version

        document.status = DocumentStatus.READY

        self.session.add(version)
        self.session.add(document)

        await self.session.commit()

        logger.info(
            "Document version activated | document_id=%s version=%s",
            document.id,
            version.version,
        )


    async def mark_failed(self, document: Document):
        document.status = DocumentStatus.FAILED

        self.session.add(document)

        await self.session.commit()

        logger.warning(
            "Document processing failed | document_id=%s",
            document.id,
        )


    async def mark_processing(self, document: Document):
        document.status = DocumentStatus.PROCESSING

        self.session.add(document)

        await self.session.commit()


    async def get_documents(self, workspace_id: int) -> list[Document]:
        return await self.documents_repo.get_all(workspace_id)


    async def update_document(
        self,
        document: Document,
        data: DocumentUpdateRequest,
    ) -> Document:
        document.title = data.title.strip()

        self.session.add(document)

        await self.session.commit()
        await self.session.refresh(document)

        logger.info(
            "Document updated | document_id=%s",
            document.id,
        )

        return document


    async def delete_document(self, document: Document):
        document_id = document.id

        await self.documents_repo.delete(document)

        await self.session.commit()

        logger.info(
            "Document deleted | document_id=%s",
            document_id,
        )


    async def get_versions(self, document: Document) -> list[DocumentVersion]:
        return await self.versions_repo.get_all(document.id)


    async def get_chunks(self, document: Document) -> list[DocumentChunk]:
        active_version = await self.versions_repo.get_active(document.id)

        if active_version is None:
            return []

        return await self.chunks_repo.get_all_by_version(active_version.id)