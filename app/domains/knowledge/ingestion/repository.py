from typing import Optional

from datetime import timedelta
from uuid import UUID

from sqlmodel import select, and_, func, or_, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.knowledge.ingestion.model import IngestionJob
from app.domains.knowledge.ingestion.enums import (
    IngestionJobStatus,
    IngestionStage,
)


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

    async def try_claim(
        self,
        job_id: int,
        execution_token: UUID,
        lease_duration: timedelta,
    ) -> bool:
        eligible = or_(
            IngestionJob.status == IngestionJobStatus.QUEUED,

            and_(
                IngestionJob.status == IngestionJobStatus.RETRY_WAIT,
                IngestionJob.next_retry_at <= func.now(),
            ),

            and_(
                IngestionJob.status == IngestionJobStatus.PROCESSING,
                IngestionJob.lease_expires_at <= func.now(),
            ),
        )

        statement = (
            update(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                eligible,
            )
            .values(
                status=IngestionJobStatus.PROCESSING,
                stage=IngestionStage.DOWNLOADING,
                execution_token=execution_token,
                lease_expires_at=func.now() + lease_duration,
                started_at=func.now(),
                completed_at=None,
                next_retry_at=None,
                error_code=None,
                error_message=None,
            )
            .returning(IngestionJob.id)
            .execution_options(synchronize_session=False)
        )

        result =  await self.session.exec(statement)
        return result.first() is not None