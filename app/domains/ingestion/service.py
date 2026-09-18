import asyncio
import logging
from datetime import datetime, timezone

from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.document.model import (
    Document,
    DocumentStatus,
    DocumentVersion,
)
from app.domains.document.repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from app.domains.ingestion.enums import (
    IngestionStage,
    IngestionJobStatus,
)
from app.domains.ingestion.schema import (
    ParsedDocument,
)
from app.domains.ingestion.parsers.factory import DocumentParser
from app.domains.ingestion.model import IngestionJob
from app.domains.ingestion.repository import IngestionJobRepository
from app.core.s3 import S3Client


logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(
        self,
        session: AsyncSession,
        s3: S3Client,
        parser: DocumentParser,
        jobs_repo: IngestionJobRepository,
        documents_repo: DocumentRepository,
        versions_repo: DocumentVersionRepository,
    ):
        self.session = session
        self.s3 = s3
        self.parser = parser
        self.jobs_repo = jobs_repo
        self.documents_repo = documents_repo
        self.versions_repo = versions_repo


    async def process(self, job_id: int):
        job = await self.jobs_repo.get_by_id(job_id)

        if job is None:
            raise RuntimeError(f"IngestionJob not found: {job_id}")

        version = await self.versions_repo.get_by_id(
            job.document_version_id
        )

        if version is None:
            raise RuntimeError(
                "DocumentVersion not found "
                f"| version_id={job.document_version_id}"
            )

        document = await self.documents_repo.get_raw_by_id(
            version.document_id
        )

        if document is None:
            raise RuntimeError(
                "Document not found "
                f"| document_id={version.document_id}"
            )

        try:
            await self._start_job(job, document)

            file_bytes = await self._download(job, version)

            parsed_document = await self._parse(
                job,
                version,
                file_bytes,
            )

            logger.info(
                "Document parsed "
                "| job_id=%s sections=%s chars=%s",
                job.id,
                len(parsed_document.sections),
                sum(
                    len(section.text)
                    for section in parsed_document.sections
                ),
            )
            print(parsed_document)
            # 이거 다음 단계에서 구현할 것들임
            #
            # chunks = await self._chunk(
            #     parsed
            # )
            #
            # vectors = await self._embed(
            #     chunks
            # )
            #
            # await self._index(...)
            #
            # await self._complete(...)

            logger.info(
                "Ingestion download completed "
                "| job_id=%s document_id=%s version=%s size=%s",
                job.id,
                document.id,
                version.version,
                len(file_bytes),
            )

        except Exception as exc:
            await self._fail_job(job, document, exc)

            raise


    async def _parse(
        self,
        job: IngestionJob,
        version: DocumentVersion,
        file_bytes: bytes,
    ) -> ParsedDocument:
        job.stage = IngestionStage.PARSING

        self.session.add(job)
        await self.session.commit()

        parsed = await asyncio.to_thread(
            self.parser.parse,
            version.mime_type,
            file_bytes
        )

        if not parsed.sections:
            raise RuntimeError("No text extracted from document")

        return parsed


    async def _start_job(
        self,
        job: IngestionJob,
        document: Document,
    ):
        job.status = IngestionJobStatus.PROCESSING
        job.stage = IngestionStage.DOWNLOADING
        job.started_at = datetime.now(timezone.utc)

        document.status = DocumentStatus.PROCESSING

        self.session.add(job)
        self.session.add(document)

        await self.session.commit()

        logger.info(
            "Ingestion started "
            "| job_id=%s document_id=%s",
            job.id,
            document.id,
        )

    async def _download(
        self,
        job: IngestionJob,
        version: DocumentVersion,
    ) -> bytes:
        if version.s3_key is None:
            raise RuntimeError(
                "S3 key is missing "
                f"| version_id={version.id}"
            )

        job.stage = IngestionStage.DOWNLOADING

        self.session.add(job)
        await self.session.commit()

        return await self.s3.download_file(version.s3_key)


    async def _fail_job(
        self,
        job: IngestionJob,
        document: Document,
        exc: Exception,
    ):
        try:
            job.status = IngestionJobStatus.FAILED
            job.error_message = str(exc)[:2000]
            job.completed_at = datetime.now(timezone.utc)

            document.status = DocumentStatus.FAILED

            self.session.add(job)
            self.session.add(document)

            await self.session.commit()

            logger.exception(
                "Ingestion failed "
                "| job_id=%s document_id=%s stage=%s",
                job.id,
                document.id,
                job.stage,
            )

        except Exception:
            await self.session.rollback()

            logger.exception(
                "Failed to persist ingestion failure "
                "| job_id=%s",
                job.id,
            )


    async def _complete_job(
        self,
        job: IngestionJob,
        document: Document,
        version: DocumentVersion,
    ):
        version.is_active = True

        document.current_version = version.version

        document.status = DocumentStatus.READY

        job.status = IngestionJobStatus.COMPLETED

        job.completed_at = datetime.now(timezone.utc)

        self.session.add(version)
        self.session.add(document)
        self.session.add(job)

        await self.session.commit()

        logger.info(
            "Ingestion completed "
            "| job_id=%s document_id=%s version=%s",
            job.id,
            document.id,
            version.version,
        )