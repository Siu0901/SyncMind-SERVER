import asyncio
import logging
from datetime import datetime, timezone

from app.core.config import get_settings

from qdrant_client import models
from qdrant_client import AsyncQdrantClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.document.model import (
    Document,
    DocumentStatus,
    DocumentVersion,
    DocumentChunk,
)
from app.domains.document.repository import (
    DocumentRepository,
    DocumentChunkRepository,
    DocumentVersionRepository,
)
from app.domains.document.exceptions import (
    DocumentNotFoundError,
    DocumentVersionNotFoundError,
)
from app.domains.ingestion.enums import (
    IngestionStage,
    IngestionJobStatus,
)
from app.domains.ingestion.schema import (
    ParsedDocument,
)
from app.domains.ingestion.exceptions import (
    NoChunksError,
    NoTextExtractedError,
    NoS3KeyVersionsError,
    IngestionJobNotFoundError,
)
from app.domains.ingestion.parsers.factory import DocumentParser
from app.domains.ingestion.chunker.document_chunker import DocumentChunker
from app.domains.ingestion.embedding.port import EmbeddingPort
from app.domains.ingestion.model import IngestionJob
from app.domains.ingestion.repository import IngestionJobRepository
from app.core.s3 import S3Client


logger = logging.getLogger(__name__)


settings = get_settings()


class IngestionService:
    def __init__(
        self,
        session: AsyncSession,
        qdrant: AsyncQdrantClient,
        s3: S3Client,
        parser: DocumentParser,
        chunker: DocumentChunker,
        embedding: EmbeddingPort,
        jobs_repo: IngestionJobRepository,
        documents_repo: DocumentRepository,
        chunks_repo: DocumentChunkRepository,
        versions_repo: DocumentVersionRepository,
    ):
        self.session = session
        self.qdrant = qdrant
        self.s3 = s3
        self.parser = parser
        self.chunker = chunker
        self.embedding = embedding
        self.jobs_repo = jobs_repo
        self.documents_repo = documents_repo
        self.chunks_repo = chunks_repo
        self.versions_repo = versions_repo


    async def process(self, job_id: int):
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

        try:
            await self._start_job(
                job,
                document
            )

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

            self._print_chunks(chunks) # 디버깅, 로그도 좀 만들자

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
                job,
                document,
                version,
            )

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
        self.session.add(job)
        await self.session.commit()

        chunk_data_list = self.chunker.chunk(parsed_document)

        if not chunk_data_list:
            raise NoChunksError()

        chunks = [
            DocumentChunk(
                document_version_id=version.id,
                chunk_index=index,
                content=data.content,
                token_count=data.token_count,
                page_number=data.page_number,
                section=data.section,
                chunk_metadata=data.metadata,
            )
            for index, data in enumerate(chunk_data_list)
        ]

        await self.chunks_repo.create_many(chunks)

        await self.session.commit()

        logger.info(
            "Document chunked "
            "| job_id=%s version_id=%s chunks=%s",
            job.id,
            version.id,
            len(chunks),
        )

        return chunks


    async def _embed(
        self,
        job: IngestionJob,
        chunks: list[DocumentChunk],
    ) -> list[list[float]]:
        job.stage = IngestionStage.EMBEDDING

        self.session.add(job)
        await self.session.commit()

        texts = [chunk.content for chunk in chunks]

        embeddings = await self.embedding.embed(texts)

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
        job.stage = IngestionStage.INDEXING

        self.session.add(job)
        await self.session.commit()

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

        await self.qdrant.upsert(
            collection_name=settings.QDRANT_COLLECTION,
            points=points,
            wait=True,
        )

        logger.info(
            "Document indexed "
            "| job_id=%s version_id=%s points=%s",
            job.id,
            version.id,
            len(points),
        )

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
            raise NoS3KeyVersionsError(version.id)

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
        active_version = await self.versions_repo.get_active(document.id)

        if (
            active_version is not None
            and active_version.id != version.id
        ):
            active_version.is_active = False
            self.session.add(active_version)

        version.is_active = True

        document.current_version = version.version

        document.status = DocumentStatus.READY

        job.status = IngestionJobStatus.COMPLETED

        job.stage = None

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