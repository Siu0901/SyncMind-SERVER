from typing import Optional

from sqlmodel import select, desc
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.workspace.model import (
    WorkSpaceMember,
    WorkSpace
)


class WorkspaceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session


    async def get_by_id(self, workspace_id: int) -> Optional[WorkSpace]:
        statement = select(WorkSpace).where(WorkSpace.id == workspace_id)
        return (await self.session.exec(statement)).first()


    async def get_by_user_id(self, user_id: int) -> list[WorkSpace]:
        statement = select(WorkSpace).join(
            WorkSpaceMember,
            WorkSpaceMember.workspace_id == WorkSpace.id,
        ).where(
            WorkSpaceMember.user_id == user_id
        ).order_by(
            desc(WorkSpace.created_at)
        )

        results = await self.session.exec(statement)
        return list(results.all())

    async def create(self, workspace: WorkSpace):
        self.session.add(workspace)
        await self.session.flush()
        await self.session.refresh(workspace)

    async def delete(self, workspace: WorkSpace):
        await self.session.delete(workspace)


class WorkspaceMemberRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_member(
        self,
        workspace_id: int,
        user_id: int,
    ) -> Optional[WorkSpaceMember]:
        statement = select(
            WorkSpaceMember
        ).where(
            WorkSpaceMember.workspace_id
            == workspace_id,
            WorkSpaceMember.user_id
            == user_id,
        )

        result = await self.session.exec(statement)

        return result.first()

    async def get_all_members(
        self,
        workspace_id: int,
    ) -> list[WorkSpaceMember]:
        statement = (
            select(WorkSpaceMember)
            .where(
                WorkSpaceMember.workspace_id
                == workspace_id
            )
            .order_by(
                WorkSpaceMember.joined_at
            )
        )

        result = await self.session.exec(statement)

        return list(result.all())

    async def create(
        self,
        member: WorkSpaceMember,
    ) -> WorkSpaceMember:
        self.session.add(member)

        await self.session.flush()
        await self.session.refresh(member)

        return member

    async def delete(self,member: WorkSpaceMember):
        await self.session.delete(member)