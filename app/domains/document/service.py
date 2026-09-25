import logging

import hashlib
from pathlib import Path
from uuid import uuid4

from arq import ArqRedis

from fastapi import UploadFile

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
from app.domains.document.vector_repository import QdrantVectorRepository
from app.domains.document.schema import (
    DocumentCreateData,
    DocumentUpdateRequest,
    DocumentVersionCreateData,
)
from app.domains.document.exceptions import (
    DocumentNotFoundError,
    DocumentNotReadyError,
    DocumentVersionNotFoundError,
    DuplicateDocumentError,
)
from app.domains.ingestion.repository import IngestionJobRepository
from app.domains.ingestion.model import IngestionJob
from app.domains.ingestion.enums import IngestionJobStatus
from app.core.exception.exceptions import AppException
from app.core.config import get_settings
from app.core.s3 import S3Client


logger = logging.getLogger(__name__)


settings = get_settings()


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        documents_repo: DocumentRepository,
        versions_repo: DocumentVersionRepository,
        chunks_repo: DocumentChunkRepository,
        vector_repo: QdrantVectorRepository,
        s3: S3Client,
    ):
        self.session = session
        self.documents_repo = documents_repo
        self.versions_repo = versions_repo
        self.chunks_repo = chunks_repo
        self.vector_repo = vector_repo
        self.s3 = s3


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

        versions = await self.versions_repo.get_all(document_id)

        s3_keys = [
            version.s3_key
            for version in versions
            if version.s3_key is not None
        ]

        try:
            await self.s3.delete_files(s3_keys)

            await self.vector_repo.delete_by_document(document_id)

            await self.documents_repo.delete(document)

            await self.session.commit()

        except Exception:
            await self.session.rollback()

            logger.exception(
                "Document deletion failed | document_id=%s",
                document_id,
            )

            raise

        logger.info(
            "Document deleted | document_id=%s s3_files=%s",
            document_id,
            len(s3_keys),
        )


    async def get_versions(self, document: Document) -> list[DocumentVersion]:
        result =  await self.versions_repo.get_all(document.id)
        if result is None:
            raise DocumentVersionNotFoundError()

        return result


    async def get_chunks(self, document: Document) -> list[DocumentChunk]:
        active_version = await self.versions_repo.get_active(document.id)

        if active_version is None:
            return []

        return await self.chunks_repo.get_all_by_version(active_version.id)


class DocumentUploadService:
    def __init__(
        self,
        session: AsyncSession,
        redis: ArqRedis,
        s3: S3Client,
        documents_repo: DocumentRepository,
        versions_repo: DocumentVersionRepository,
        ingestion_repo: IngestionJobRepository
    ):
        self.session = session
        self.redis = redis
        self.s3 = s3
        self.documents_repo = documents_repo
        self.versions_repo = versions_repo
        self.ingestion_repo = ingestion_repo


    @staticmethod
    async def _prepare_file(file: UploadFile) -> tuple[int, str]:
        if file.filename is None:
            raise AppException(
                "파일 이름이 존재하지 않습니다",
                400,
            )

        if file.content_type not in settings.ALLOWED_CONTENT_TYPES:
            raise AppException(
                "지원하지 않은 파일 형식 입니다.",
                415
            )

        sha256 = hashlib.sha256()

        size = 0

        while chunk := await file.read(1024 * 1024):
            size += len(chunk)

            if size > settings.MAX_FILE_SIZE:
                raise AppException(
                    "파일 크기는 20MB를 초과할 수 없습니다.",
                    413,
                )

            sha256.update(chunk)

        await file.seek(0)

        return size, sha256.hexdigest()


    @staticmethod
    def _create_s3_key(
        workspace_id: int,
        filename: str,
    ) -> str:
        safe_filename = Path(filename).name

        upload_id = str(uuid4())

        return (
            f"workspaces/"
            f"{workspace_id}/"
            f"documents/"
            f"{upload_id}/"
            f"{safe_filename}"
        )


    async def upload(
        self,
        workspace_id: int,
        file: UploadFile,
    ) -> Document:
        file_size, content_hash = await self._prepare_file(file)

        exist = await self.versions_repo.exists_by_content_hash(
            content_hash, workspace_id
        )

        if exist:
            raise DuplicateDocumentError()

        filename = Path(file.filename).name

        s3_key = self._create_s3_key(workspace_id, filename)

        await self.s3.upload_file(
            file=file.file,
            key=s3_key,
            content_type=file.content_type,
        )

        try:
            document = Document(
                workspace_id=workspace_id,
                source_id=None,
                external_id=None,
                title=filename,
                status=DocumentStatus.PENDING,
                current_version=None,
            )
            await self.documents_repo.create(document)

            version = DocumentVersion(
                document_id=document.id,
                version=1,
                content_hash=content_hash,
                s3_key=s3_key,
                mime_type=file.content_type,
                file_size=file_size,
                source_updated_at=None,
                is_active=False,
            )
            await self.versions_repo.create(version)

            job = IngestionJob(
                document_version_id=version.id,
                status=IngestionJobStatus.QUEUED,
                retry_count=0,
            )
            await self.ingestion_repo.create(job)

            await self.session.commit()

        except Exception:
            await self.session.rollback()

            await self.s3.delete_file(s3_key)

            raise

        try:
            await self.redis.enqueue_job(
                "process_document",
                job.id,
                _job_id=f"ingestion:{job.id}",
            )

        except Exception:
            logger.exception(
                "Failed to enqueue ingestion | job_id=%s",
                job.id,
            )

            # 지금은 일단 그대로 raise 하자
            # 나중에 outbox/retry 구조로 개선 ㄱㄱ.
            raise

        logger.info(
            "Document uploaded | document_id=%s version_id=%s job_id=%s",
            document.id,
            version.id,
            job.id,
        )

        return document