from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.ingestion.model import IngestionJob


class IngestionJobRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, job: IngestionJob) -> IngestionJob:
        self.session.add(job)

        await self.session.flush()
        await self.session.refresh(job)

        return job

    async def get_by_id(self, job_id: int) -> Optional[IngestionJob]:
        statement = select(IngestionJob).where(IngestionJob.id == job_id)
        return (await self.session.exec(statement)).first()