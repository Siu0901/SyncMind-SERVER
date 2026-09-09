import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.auth.exceptions import UserNotFoundError
from app.domains.workspace.exceptions import (
    CannotChangeWorkspaceOwnerRoleError,
    CannotRemoveWorkspaceOwnerError,
    WorkspaceMemberAlreadyExistsError,
    WorkspaceMemberNotFoundError,
    WorkspaceNotFoundError,
    WorkspacePermissionDeniedError,
)
from app.domains.user.model import User
from app.domains.user.repository import UserRepository
from app.domains.workspace.enums import WorkspaceRole
from app.domains.workspace.model import (
    WorkSpace,
    WorkSpaceMember,
)
from app.domains.workspace.repository import (
    WorkspaceMemberRepository,
    WorkspaceRepository,
)
from app.domains.workspace.schema import (
    WorkspaceCreateRequest,
    WorkspaceMemberAddRequest,
    WorkspaceMemberRoleUpdateRequest,
    WorkspaceUpdateRequest,
)


logger = logging.getLogger(__name__)


class WorkspaceService:
    def __init__(
        self,
        session: AsyncSession,
        workspaces_repo: WorkspaceRepository,
        members_repo: WorkspaceMemberRepository,
        users_repo: UserRepository,
    ):
        self.session = session
        self.workspaces_repo = workspaces_repo
        self.members_repo = members_repo
        self.users_repo = users_repo


    async def create_workspace(
        self,
        user: User,
        data: WorkspaceCreateRequest,
    ) -> WorkSpace:
        workspace = WorkSpace(name=data.name.strip())

        await self.workspaces_repo.create(workspace)

        owner = WorkSpaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.OWNER,
        )

        await self.members_repo.create(owner)

        await self.session.commit()

        logger.info(
            "Workspace created | workspace_id=%s user_id=%s",
            workspace.id,
            user.id,
        )

        return workspace


    async def get_my_workspace(self, user: User) -> list[WorkSpace]:
        return await self.workspaces_repo.get_by_user_id(user.id)


    @staticmethod
    async def get_workspace(workspace: WorkSpace) -> WorkSpace:
        return workspace


    async def update_workspace(
        self,
        workspace: WorkSpace,
        data: WorkspaceUpdateRequest,
    ) -> WorkSpace:
        workspace.name = data.name.strip()

        self.session.add(workspace)

        await self.session.commit()
        await self.session.refresh(workspace)

        logger.info(
            "Workspace updated | workspace_id=%s",
            workspace.id,
        )

        return workspace


    async def delete_workspace(self, workspace: WorkSpace):
        workspace_id = workspace.id

        await self.workspaces_repo.delete(workspace)

        await self.session.commit()

        logger.info(
            "Workspace deleted | workspace_id=%s",
            workspace_id,
        )


    async def get_member(self, workspace: WorkSpace) -> list[WorkSpaceMember]:
        return await self.members_repo.get_all_members(workspace.id)


    async def add_member(
        self,
        workspace_id: int,
        data: WorkspaceMemberAddRequest
    ) -> WorkSpaceMember:
        user = await self.users_repo.get_by_id(data.user_id)

        if not user:
            raise UserNotFoundError(data.user_id)

        existing_member = await self.members_repo.get_member(
            workspace_id, data.user_id
        )

        if existing_member:
            raise WorkspaceMemberAlreadyExistsError()

        workspace_member = WorkSpaceMember(
            workspace_id=workspace_id,
            user_id=data.user_id,
            role=data.role,
        )

        await self.members_repo.create(workspace_member)

        await self.session.commit()

        logger.info(
            "Workspace member added | workspace_id=%s user_id=%s role=%s",
            workspace_id,
            data.user_id,
            data.role.value,
        )

        return workspace_member


    async def update_member_role(
        self,
        member: WorkSpaceMember,
        data: WorkspaceMemberRoleUpdateRequest
    ) -> WorkSpaceMember:
        if member.role == WorkspaceRole.OWNER:
            raise CannotChangeWorkspaceOwnerRoleError()

        if data.role == WorkspaceRole.OWNER:
            raise CannotChangeWorkspaceOwnerRoleError()

        member.role = data.role

        self.session.add(member)

        await self.session.commit()
        await self.session.refresh(member)

        logger.info(
            "Workspace member role updated | workspace_id=%s user_id=%s role=%s",
            member.workspace_id,
            member.user_id,
            member.role,
        )

        return member

    async def remove_member(
        self,
        requester: WorkSpaceMember,
        target: WorkSpaceMember,
    ):
        if target.role == WorkspaceRole.OWNER:
            raise CannotRemoveWorkspaceOwnerError()

        if (
            requester.role == WorkspaceRole.ADMIN
            and target.role == WorkspaceRole.ADMIN
        ):
            raise WorkspacePermissionDeniedError()

        workspace_id = target.workspace_id
        user_id = target.user_id

        await self.members_repo.delete(target)

        await self.session.commit()

        logger.info(
            "Workspace member removed | workspace_id=%s user_id=%s",
            workspace_id,
            user_id,
        )