import logging

from app.core.database import get_worker_session
from app.core.qdrant import get_qdrant
from app.domains.document.repository import (
    DocumentRepository,
    DocumentChunkRepository,
    DocumentVersionRepository,
)
from app.domains.ingestion.repository import (
    IngestionJobRepository,
)
from app.domains.ingestion.service import (
    IngestionService,
)
from app.domains.ingestion.parsers.factory import DocumentParser
from app.domains.ingestion.chunker.document_chunker import DocumentChunker


logger = logging.getLogger(__name__)


async def process_document(
    ctx,
    job_id: int,
):
    async with get_worker_session() as session:
        service = IngestionService(
            session=session,
            qdrant=get_qdrant(),
            s3=ctx["s3"],
            chunker=DocumentChunker(),
            embedding=ctx["embedding"],
            parser=DocumentParser(),
            jobs_repo=IngestionJobRepository(session),
            documents_repo=DocumentRepository(session),
            chunks_repo=DocumentChunkRepository(session),
            versions_repo=DocumentVersionRepository(session),
        )

        await service.process(job_id)