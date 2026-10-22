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


    @staticmethod
    def _owned_conditions(
        job_id: int,
        execution_token: UUID,
    ):
        return (
            IngestionJob.id == job_id,
            IngestionJob.status == IngestionJobStatus.PROCESSING,
            IngestionJob.execution_token == execution_token,
            IngestionJob.lease_expires_at > func.now(),
        )


    async def _update_owned(
        self,
        job_id: int,
        owner_token: UUID,
        **values,
    ) -> bool:
        statement = (
            update(IngestionJob)
            .where(*self._owned_conditions(job_id, owner_token))
            .values(**values)
            .returning(IngestionJob.id)
            .execution_options(synchronize_session=False)
        )

        result = await self.session.exec(statement)

        return result.first() is not None


    async def lock_owned(
        self,
        job_id: int,
        execution_token: UUID,
    ) -> bool:
        statement = (
            select(IngestionJob.id)
            .where(*self._owned_conditions(job_id, execution_token))
            .with_for_update()
        )

        result = await self.session.exec(statement)

        return result.first() is not None


    async def update_stage_if_owned(
        self,
        job_id: int,
        execution_token: UUID,
        stage: IngestionStage,
        lease_duration: timedelta,
    ) -> bool:
        return await self._update_owned(
            job_id,
            execution_token,
            stage=stage,
            lease_expires_at=func.now() + lease_duration,
        )


    async def complete_if_owned(
        self,
        job_id: int,
        execution_token: UUID,
    ) -> bool:
        return await self._update_owned(
            job_id,
            execution_token,
            status=IngestionJobStatus.COMPLETED,
            stage=None,
            completed_at=func.now(),
            next_retry_at=None,
            lease_expires_at=None,
            execution_token=None,
            error_code=None,
            error_message=None,
        )


    async def fail_if_owned(
        self,
        job_id: int,
        execution_token: UUID,
        error_code: str,
        error_message: str,
    ) -> bool:
        return await self._update_owned(
            job_id,
            execution_token,
            status=IngestionJobStatus.FAILED,
            completed_at=func.now(),
            next_retry_at=None,
            lease_expires_at=None,
            execution_token=None,
            error_code=error_code,
            error_message=error_message[:2000],
        )