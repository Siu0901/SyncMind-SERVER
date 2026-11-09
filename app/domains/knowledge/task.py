import logging

from uuid import uuid4

from app.core.config import get_settings
from app.core.database import get_worker_session
from app.core.qdrant import get_qdrant
from app.domains.knowledge.document.repository import (
    DocumentRepository,
    DocumentChunkRepository,
    DocumentVersionRepository,
)
from app.domains.knowledge.vector_repository import QdrantVectorRepository
from app.domains.knowledge.ingestion.repository import (
    IngestionJobRepository,
)
from app.domains.knowledge.services.ingestion import (
    IngestionService,
)
from app.domains.knowledge.ingestion.parsers.factory import DocumentParser
from app.domains.knowledge.ingestion.chunker.document_chunker import DocumentChunker
from app.domains.knowledge.operation.repository import (
    DocumentOperationRepository,
)
from app.domains.knowledge.services.lifecycle import (
    DocumentLifecycleService,
)


logger = logging.getLogger(__name__)


settings = get_settings()


def create_lifecycle(session, ctx):
    return DocumentLifecycleService(
        session=session,
        s3=ctx["s3"],
        vectors=QdrantVectorRepository(get_qdrant()),
        redis=ctx["redis"],
    )


async def process_document(ctx, job_id: int):
    async with get_worker_session() as session:
        lifecycle = create_lifecycle(session, ctx)

        service = IngestionService(
            session=session,
            s3=ctx["s3"],
            chunker=DocumentChunker(),
            embedding=ctx["embedding"],
            parser=DocumentParser(),
            jobs_repo=IngestionJobRepository(session),
            documents_repo=DocumentRepository(session),
            chunks_repo=DocumentChunkRepository(session),
            vectors_repo=QdrantVectorRepository(get_qdrant()),
            versions_repo=DocumentVersionRepository(session),
            operations_repo=DocumentOperationRepository(session),
            lifecycle=lifecycle,
        )

        await service.process(job_id)


async def maintain_document(ctx, document_id: int):
    async with get_worker_session() as session:
        lifecycle = create_lifecycle(session, ctx)
        await lifecycle.maintain(document_id)


async def recover_documents(ctx):
    async with get_worker_session() as session:
        repository = DocumentOperationRepository(session)
        document_ids = await repository.get_due_ids(limit=100)

    for document_id in document_ids:
        await ctx["redis"].enqueue_job(
            "maintain_document",
            document_id,
            _job_id=f"maintenance:{document_id}:{uuid4().hex}",
            _queue_name=settings.document_queue_name,
        )