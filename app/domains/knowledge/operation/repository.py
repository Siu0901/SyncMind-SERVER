from typing import Any

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.knowledge.operation.model import (
    DocumentOperation,
    OperationStatus,
)


class DocumentOperationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        operation: DocumentOperation,
    ) -> DocumentOperation:
        self.session.add(operation)
        await self.session.flush()
        return operation

    async def get(
        self,
        document_id: int,
        workspace_id: int,
    ) -> DocumentOperation | None:
        statement = select(DocumentOperation).where(
            DocumentOperation.document_id == document_id,
            DocumentOperation.workspace_id == workspace_id,
        )
        return (await self.session.exec(statement)).first()

    async def lock(
        self,
        document_id: int,
        skip_locked: bool = False,
    ) -> DocumentOperation | None:
        statement = (
            select(DocumentOperation)
            .where(DocumentOperation.document_id == document_id)
            .with_for_update(skip_locked=skip_locked)
            .execution_options(populate_existing=True)
        )
        return (await self.session.exec(statement)).first()

    async def change(
        self,
        operation: DocumentOperation,
        **values: Any,
    ):
        for name, value in values.items():
            setattr(operation, name, value)

        self.session.add(operation)
        await self.session.flush()

    async def get_due_ids(self, limit: int = 100) -> list[int]:
        statement = (
            select(DocumentOperation.document_id)
            .where(
                DocumentOperation.status != OperationStatus.READY,
                DocumentOperation.next_run_at <= func.now(),
            )
            .order_by(DocumentOperation.next_run_at)
            .limit(limit)
        )
        return list((await self.session.exec(statement)).all())