import asyncio
import logging

from datetime import datetime, timezone, timedelta
from uuid import uuid4, UUID

from app.core.config import get_settings

from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.knowledge.document.model import (
    Document,
    DocumentStatus,
    DocumentVersion,
    DocumentChunk,
)
from app.domains.knowledge.document.repository import (
    DocumentRepository,
    DocumentChunkRepository,
    DocumentVersionRepository,
)
from app.domains.knowledge.vector_repository import QdrantVectorRepository
from app.domains.knowledge.document.exceptions import (
    DocumentNotFoundError,
    DocumentVersionNotFoundError,
)
from app.domains.knowledge.ingestion.enums import (
    IngestionStage,
    IngestionJobStatus,
)
from app.domains.knowledge.ingestion.schema import (
    ParsedDocument,
)
from app.domains.knowledge.ingestion.exceptions import (
    NoChunksError,
    NoTextExtractedError,
    NoS3KeyVersionsError,
    IngestionJobNotFoundError,
    IngestionLeaseLostError,
)
from app.domains.knowledge.ingestion.parsers.factory import DocumentParser
from app.domains.knowledge.ingestion.chunker.document_chunker import DocumentChunker
from app.infra.embedding.port import EmbeddingPort
from app.domains.knowledge.ingestion.model import IngestionJob
from app.domains.knowledge.ingestion.repository import IngestionJobRepository
from app.core.s3 import S3Client


logger = logging.getLogger(__name__)


settings = get_settings()


class IngestionService:

    LEASE_DURATION = timedelta(seconds=900)

    def __init__(
        self,
        session: AsyncSession,
        s3: S3Client,
        parser: DocumentParser,
        chunker: DocumentChunker,
        embedding: EmbeddingPort,
        jobs_repo: IngestionJobRepository,
        documents_repo: DocumentRepository,
        chunks_repo: DocumentChunkRepository,
        vectors_repo: QdrantVectorRepository,
        versions_repo: DocumentVersionRepository,
    ):
        self.session = session
        self.s3 = s3
        self.parser = parser
        self.chunker = chunker
        self.embedding = embedding
        self.jobs_repo = jobs_repo
        self.documents_repo = documents_repo
        self.chunks_repo = chunks_repo
        self.vectors_repo = vectors_repo
        self.versions_repo = versions_repo
        self.execution_token: UUID = uuid4()


    async def process(self, job_id: int):
        self.execution_token = uuid4()

        job = await self.jobs_repo.get_by_id(job_id)

        if job is None:
            raise IngestionJobNotFoundError(job_id)

        version = await self.versions_repo.get_by_id(
            job.document_version_id
        )

        if version is None:
            raise DocumentVersionNotFoundError()

        document = await self.documents_repo.get_raw_by_id(
            version.document_id
        )

        if document is None:
            raise DocumentNotFoundError()

        document_id = document.id

        started = await self._start_job(job_id, document_id)

        if not started:
            logger.info(
                "Ingestion skipped | job_id=%s",
                job_id,
            )
            return

        try:
            file_bytes = await self._download(
                job,
                version
            )

            parsed_document = await self._parse(
                job,
                version,
                file_bytes,
            )

            chunks = await self._chunk(
                job,
                version,
                parsed_document,
            )

            # self._print_chunks(chunks) # 디버깅, 로그도 좀 만들자

            vectors = await self._embed(
                job,
                chunks
            )

            await self._index(
                job,
                document,
                version,
                chunks,
                vectors,
            )

            await self._complete_job(
                job_id,
                document_id,
                version,
            )

        except IngestionLeaseLostError:
            await self.session.rollback()

            logger.info(
                "Ingestion stopped: ownership lost | job_id=%s",
                job_id,
            )

        except Exception as exc:
            await self.session.rollback()

            try:
                await self._fail_job(
                    job_id,
                    document_id,
                    exc,
                )

            except IngestionLeaseLostError:
                await self.session.rollback()

                logger.info(
                    "Failure update skipped: ownership lost | job_id=%s",
                    job_id,
                )

            except Exception:
                await self.session.rollback()

                logger.exception(
                    "Failed to persist ingestion failure | job_id=%s",
                    job_id,
                )

            raise


    async def _start_job(
        self,
        job_id: int,
        document_id: int,
    ) -> bool:
        claimed = await self.jobs_repo.try_claim(
            job_id,
            self.execution_token,
            self.LEASE_DURATION,
        )

        if not claimed:
            await self.session.rollback()
            return False

        document = await self.documents_repo.get_for_update(
            document_id
        )

        if document is None:
            raise DocumentNotFoundError()

        await self.documents_repo.mark_processing(document)

        await self.session.commit()

        return True


    async def _download(
        self,
        job: IngestionJob,
        version: DocumentVersion,
    ) -> bytes:
        if version.s3_key is None:
            raise NoS3KeyVersionsError(version.id)

        await self._set_stage(
            job.id,
            IngestionStage.DOWNLOADING
        )

        return await self.s3.download_file(version.s3_key)


    async def _parse(
        self,
        job: IngestionJob,
        version: DocumentVersion,
        file_bytes: bytes,
    ) -> ParsedDocument:
        await self._set_stage(
            job.id,
            IngestionStage.PARSING,
        )

        parsed = await asyncio.to_thread(
            self.parser.parse,
            version.mime_type,
            file_bytes
        )

        if not parsed.sections:
            raise NoTextExtractedError()

        logger.info(
            "Document parsed "
            "| job_id=%s sections=%s chars=%s",
            job.id,
            len(parsed.sections),
            sum(
                len(section.text)
                for section in parsed.sections
            ),
        )

        return parsed


    async def _chunk(
        self,
        job: IngestionJob,
        version: DocumentVersion,
        parsed_document: ParsedDocument,
    ) -> list[DocumentChunk]:
        await self._set_stage(
            job.id,
            IngestionStage.CHUNKING,
        )

        chunk_data = await asyncio.to_thread(
            self.chunker.chunk,
            parsed_document,
        )

        if not chunk_data:
            raise NoChunksError()

        await self._ensure_owned(job.id)

        chunks = await self.chunks_repo.get_or_create_for_version(
            version.id,
            chunk_data,
        )

        await self.session.commit()

        return chunks


    async def _embed(
        self,
        job: IngestionJob,
        chunks: list[DocumentChunk],
    ) -> list[list[float]]:
        await self._set_stage(
            job.id,
            IngestionStage.EMBEDDING,
        )

        texts = [chunk.content for chunk in chunks]

        embeddings = await self.embedding.embed_document(texts)

        if len(embeddings) != len(chunks):
            raise RuntimeError("Embedding count does not match chunk count")

        logger.info(
            "Document embedded "
            "| job_id=%s chunks=%s",
            job.id,
            len(chunks),
        )

        return embeddings


    async def _index(
        self,
        job: IngestionJob,
        document: Document,
        version: DocumentVersion,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ):
        await self._set_stage(
            job.id,
            IngestionStage.INDEXING,
        )

        await self.vectors_repo.upsert_chunks(
            document=document,
            version=version,
            chunks=chunks,
            embeddings=embeddings,
        )

        logger.info(
            "Document indexed "
            "| job_id=%s version_id=%s points=%s",
            job.id,
            version.id,
            len(embeddings),
        )


    async def _complete_job(
        self,
        job_id: int,
        document_id: int,
        version: DocumentVersion,
    ):
        await self._ensure_owned(job_id)

        document = await self.documents_repo.get_for_update(
            document_id
        )

        if document is None:
            raise DocumentNotFoundError()

        can_activate = (
            document.current_version is None
            or version.version >= document.current_version
        )

        if can_activate:
            await self.versions_repo.activate(version)

            await self.documents_repo.mark_ready(
                document,
                version.version,
            )

        completed = await self.jobs_repo.complete_if_owned(
            job_id,
            self.execution_token,
        )

        if not completed:
            raise IngestionLeaseLostError(job_id)

        await self.session.commit()


    async def _fail_job(
        self,
        job_id: int,
        document_id: int,
        exc: Exception,
    ):
        await self._ensure_owned(job_id)

        document = await self.documents_repo.get_for_update(
            document_id
        )

        if document is None:
            raise DocumentNotFoundError()

        await self.documents_repo.mark_ingestion_failed(document)

        failed = await self.jobs_repo.fail_if_owned(
            job_id,
            self.execution_token,
            error_code="PROCESSING_ERROR",
            error_message=str(exc),
        )

        if not failed:
            raise IngestionLeaseLostError(job_id)

        await self.session.commit()


    async def _ensure_owned(self, job_id: int):
        owned = await self.jobs_repo.lock_owned(
            job_id,
            self.execution_token,
        )

        if not owned:
            raise IngestionLeaseLostError(job_id)


    async def _set_stage(
        self,
        job_id: int,
        stage: IngestionStage,
    ):
        updated = await self.jobs_repo.update_stage_if_owned(
            job_id,
            self.execution_token,
            stage,
            self.LEASE_DURATION,
        )

        if not updated:
            raise IngestionLeaseLostError(job_id)

        await self.session.commit()


    # 디버깅용
    @staticmethod
    def _print_chunks(chunks: list, preview: int | None = None, min_tokens: int | None = None) -> None:
        if not chunks:
            print("청크 없음")
            return

        counts = [chunk.token_count for chunk in chunks]

        print("=" * 80)
        print(
            f"총 {len(chunks)}개 | 토큰 평균 {sum(counts) / len(counts):.0f}"
            f" / 최소 {min(counts)} / 최대 {max(counts)}"
        )
        if min_tokens is not None:
            short = sum(count < min_tokens for count in counts)
            print(f"{min_tokens}토큰 미만: {short}개")
        print("=" * 80)

        for chunk in chunks:
            page = chunk.page_number if chunk.page_number is not None else "-"

            header = f"[#{chunk.chunk_index}] p.{page} | {chunk.token_count} tokens"
            if chunk.section:
                header += f" | {chunk.section}"
            if min_tokens is not None and chunk.token_count < min_tokens:
                header += "짧음"

            print(header)
            print("-" * 80)

            content = chunk.content
            if preview is not None and len(content) > preview:
                content = content[:preview] + " …"

            for line in content.splitlines():
                print(f"  {line}")

            print()