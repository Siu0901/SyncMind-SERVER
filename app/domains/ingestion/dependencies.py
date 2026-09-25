from fastapi import Depends

from typing import Annotated


from app.core.dependencies import SessionDep
from app.domains.ingestion.repository import IngestionJobRepository


def get_ingestion_job_repository(
    session: SessionDep,
) -> IngestionJobRepository:
    return IngestionJobRepository(session)

IngestionJobRepositoryDep = Annotated[
    IngestionJobRepository,
    Depends(get_ingestion_job_repository),
]