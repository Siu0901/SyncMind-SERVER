import logging

from app.core.database import get_worker_session
from app.domains.document.repository import (
    DocumentRepository,
    DocumentVersionRepository,
)
from app.domains.ingestion.repository import (
    IngestionJobRepository,
)
from app.domains.ingestion.service import (
    IngestionService,
)
from app.core.s3 import S3Client


logger = logging.getLogger(__name__)


async def process_document(
    ctx,
    job_id: int,
):
    async with get_worker_session() as session:
        service = IngestionService(
            session=session,
            s3=ctx["s3"],
            jobs_repo=IngestionJobRepository(session),
            documents_repo=DocumentRepository(session),
            versions_repo=DocumentVersionRepository(session),
        )

        await service.process(job_id)