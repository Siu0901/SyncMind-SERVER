import asyncio
import logging

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from arq import ArqRedis
from fastapi import UploadFile
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.exception.exceptions import AppException
from app.core.s3 import S3Client

from app.domains.knowledge.document.exceptions import (
    DuplicateDocumentError,
)
from app.domains.knowledge.document.model import (
    Document,
    DocumentStatus,
    DocumentVersion,
)
from app.domains.knowledge.document.repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from app.domains.knowledge.ingestion.enums import IngestionJobStatus
from app.domains.knowledge.ingestion.model import IngestionJob
from app.domains.knowledge.ingestion.repository import (
    IngestionJobRepository,
)
from app.domains.knowledge.operation.model import (
    DocumentOperation,
    OperationStatus,
)
from app.domains.knowledge.operation.repository import (
    DocumentOperationRepository,
)
from app.domains.knowledge.vector_repository import (
    QdrantVectorRepository,
)


logger = logging.getLogger(__name__)


settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentLifecycleService:
    UPLOAD_TIMEOUT_SECONDS = 120
    UPLOAD_DEADLINE = timedelta(minutes=3)

    PROCESSING_DEADLINE = timedelta(minutes=15)

    DISPATCH_INTERVAL = timedelta(minutes=1)
    CLEANUP_TIMEOUT_SECONDS = 60

    DELETED_CHECK_INTERVAL = timedelta(days=1)

    def __init__(
        self,
        session: AsyncSession,
        s3: S3Client,
        vectors: QdrantVectorRepository,
        redis: ArqRedis,
    ):
        self.session = session
        self.s3 = s3
        self.vectors = vectors
        self.redis = redis

        self.operations = DocumentOperationRepository(session)
        self.documents = DocumentRepository(session)
        self.versions = DocumentVersionRepository(session)
        self.jobs = IngestionJobRepository(session)

    async def upload_prepared(
        self,
        workspace_id: int,
        file: UploadFile,
        file_size: int,
        content_hash: str,
    ) -> Document:
        exists = await self.versions.exists_by_content_hash(
            content_hash,
            workspace_id,
        )
        if exists:
            raise DuplicateDocumentError()

        filename = Path(file.filename).name
        s3_key = (
            f"workspaces/{workspace_id}/documents/"
            f"{uuid4()}/{filename}"
        )

        try:
            document = await self.documents.create(
                Document(
                    workspace_id=workspace_id,
                    title=filename,
                    status=DocumentStatus.PENDING,
                    current_version=None,
                )
            )

            version = await self.versions.create(
                DocumentVersion(
                    document_id=document.id,
                    version=1,
                    content_hash=content_hash,
                    s3_key=s3_key,
                    mime_type=file.content_type,
                    file_size=file_size,
                    is_active=False,
                )
            )

            job = await self.jobs.create(
                IngestionJob(
                    document_version_id=version.id,
                    status=IngestionJobStatus.QUEUED,
                )
            )

            document_id = document.id

            await self.operations.create(
                DocumentOperation(
                    document_id=document_id,
                    workspace_id=workspace_id,
                    title=filename,
                    ingestion_job_id=job.id,
                    s3_keys=[s3_key],
                    status=OperationStatus.UPLOADING,
                    next_run_at=utcnow() + self.UPLOAD_DEADLINE,
                )
            )

            await self.session.commit()

        except Exception:
            await self.session.rollback()
            raise

        try:
            operation = await self.operations.lock(document_id)

            if (
                operation is None
                or operation.status != OperationStatus.UPLOADING
            ):
                raise AppException("업로드가 취소되었습니다.", 409)

            async with asyncio.timeout(self.UPLOAD_TIMEOUT_SECONDS):
                await self.s3.upload_file(
                    file=file.file,
                    key=s3_key,
                    content_type=file.content_type,
                )

            await self.operations.change(
                operation,
                status=OperationStatus.QUEUED,
                next_run_at=utcnow(),
            )
            await self.session.commit()

        except Exception as exc:
            await self.session.rollback()

            try:
                await self.request_cleanup(
                    document_id,
                    reason="UPLOAD_FAILED",
                    error_message=str(exc),
                )
                await self.maintain(document_id)

            except Exception:
                logger.exception(
                    "Failed to compensate upload | document_id=%s",
                    document_id,
                )
                await self.session.rollback()

            raise

        try:
            await self.maintain(document_id)

        except Exception:
            logger.exception(
                "Initial dispatch failed | document_id=%s",
                document_id,
            )
            await self.session.rollback()

        document = await self.documents.get_raw_by_id(document_id)

        if document is None:
            raise AppException(
                "문서 처리에 실패했습니다. 작업 상태를 확인해 주세요.",
                409,
            )

        return document

    async def delete_document(self, document_id: int):
        await self.request_cleanup(
            document_id,
            reason="USER_DELETE",
        )

        try:
            await self.redis.enqueue_job(
                "maintain_document",
                document_id,
                _job_id=f"cleanup:{document_id}:{uuid4().hex}",
                _queue_name=settings.document_queue_name,
            )
        except Exception:
            logger.exception(
                "Cleanup dispatch failed | document_id=%s",
                document_id,
            )

    async def request_cleanup(
        self,
        document_id: int,
        reason: str,
        error_message: str | None = None,
        owner_token=None,
    ) -> bool:
        operation = await self.operations.lock(document_id)

        if operation is None:
            await self.session.rollback()
            raise RuntimeError(
                f"Document operation missing: {document_id}"
            )

        if operation.status in {
            OperationStatus.CLEANUP_REQUIRED,
            OperationStatus.DELETED,
        }:
            await self.session.rollback()
            return True

        if (
            reason != "USER_DELETE"
            and operation.status == OperationStatus.READY
        ):
            await self.session.rollback()
            return False

        if owner_token is not None:
            owned = await self.jobs.lock_owned(
                operation.ingestion_job_id,
                owner_token,
            )
            if not owned:
                await self.session.rollback()
                return False

        await self._mark_cleanup_locked(
            operation,
            reason=reason,
            error_message=error_message,
        )
        await self.session.commit()
        return True

    async def _mark_cleanup_locked(
        self,
        operation: DocumentOperation,
        reason: str,
        error_message: str | None,
    ):
        versions = await self.versions.get_all(
            operation.document_id
        )
        keys = list(
            dict.fromkeys(
                [
                    *operation.s3_keys,
                    *(
                        version.s3_key
                        for version in versions
                        if version.s3_key is not None
                    ),
                ]
            )
        )

        await self.operations.change(
            operation,
            status=OperationStatus.CLEANUP_REQUIRED,
            s3_keys=keys,
            reason=reason,
            error_message=(
                error_message[:2000] if error_message else None
            ),
            next_run_at=utcnow(),
        )

        await self.jobs.cancel_for_document(
            operation.document_id,
            reason,
            error_message,
        )

        await self.documents.mark_removed(
            operation.document_id,
            failed=reason != "USER_DELETE",
        )

    async def maintain(self, document_id: int):
        operation = await self.operations.lock(
            document_id,
            skip_locked=True,
        )

        if operation is None:
            await self.session.rollback()
            return

        now = utcnow()

        if (
            operation.next_run_at is None
            or operation.next_run_at > now
        ):
            await self.session.rollback()
            return

        if operation.status == OperationStatus.QUEUED:
            job_id = operation.ingestion_job_id

            if job_id is None:
                await self._mark_cleanup_locked(
                    operation,
                    reason="INTERRUPTED",
                    error_message="Ingestion job is missing",
                )
                await self.session.commit()
                await self.maintain(document_id)
                return

            await self.operations.change(
                operation,
                next_run_at=now + self.DISPATCH_INTERVAL,
            )
            await self.session.commit()

            try:
                await self.redis.enqueue_job(
                    "process_document",
                    job_id,
                    _job_id=f"ingestion:{job_id}:{uuid4().hex}",
                    _queue_name=settings.document_queue_name,
                )
            except Exception:
                logger.exception(
                    "Ingestion dispatch failed | document_id=%s",
                    document_id,
                )

            return

        if operation.status in {
            OperationStatus.UPLOADING,
            OperationStatus.PROCESSING,
        }:
            await self._mark_cleanup_locked(
                operation,
                reason="INTERRUPTED",
                error_message="Document operation deadline expired",
            )
            await self.session.commit()

            await self.maintain(document_id)
            return

        if operation.status not in {
            OperationStatus.CLEANUP_REQUIRED,
            OperationStatus.DELETED,
        }:
            await self.session.rollback()
            return

        try:
            async with asyncio.timeout(
                self.CLEANUP_TIMEOUT_SECONDS
            ):
                await self.vectors.delete_by_document(document_id)
                await self.s3.delete_files(operation.s3_keys)

            await self.documents.delete_by_id(document_id)

            await self.operations.change(
                operation,
                status=OperationStatus.DELETED,
                cleanup_attempts=0,
                cleanup_error=None,
                completed_at=operation.completed_at or utcnow(),
                next_run_at=(
                    utcnow() + self.DELETED_CHECK_INTERVAL
                ),
            )
            await self.session.commit()

        except Exception as exc:
            await self.session.rollback()

            logger.exception(
                "Document cleanup failed | document_id=%s",
                document_id,
            )

            operation = await self.operations.lock(document_id)

            if operation is None:
                await self.session.rollback()
                return

            attempts = operation.cleanup_attempts + 1
            delay = min(
                30 * (2 ** min(attempts - 1, 6)),
                1800,
            )

            await self.operations.change(
                operation,
                status=OperationStatus.CLEANUP_REQUIRED,
                cleanup_attempts=attempts,
                cleanup_error=str(exc)[:2000],
                next_run_at=utcnow() + timedelta(seconds=delay),
            )
            await self.session.commit()