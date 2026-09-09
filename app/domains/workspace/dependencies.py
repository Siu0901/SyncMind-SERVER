from typing import Annotated

from fastapi import Depends

from app.core.dependencies import SessionDep
from app.domains.workspace.exceptions import (
    WorkspaceMemberNotFoundError,
    WorkspaceNotFoundError,
    WorkspacePermissionDeniedError,
)
from app.domains.auth.dependencies import (
    CurrentUserDep,
)
from app.domains.user.dependencies import (
    UserRepositoryDep,
)
from app.domains.workspace.enums import (
    WorkspaceRole,
)
from app.domains.workspace.model import (
    WorkSpace,
    WorkSpaceMember,
)
from app.domains.workspace.repository import (
    WorkspaceMemberRepository,
    WorkspaceRepository,
)
from app.domains.workspace.service import (
    WorkspaceService,
)


def get_workspace_repository(session: SessionDep) -> WorkspaceRepository:
    return WorkspaceRepository(session)

WorkSpaceRepositoryDep = Annotated[
    WorkspaceRepository,
    Depends(get_workspace_repository)
]

def get_workspace_member_repository(session: SessionDep) -> WorkspaceMemberRepository:
    return WorkspaceMemberRepository(session)

WorkSpaceMemberRepositoryDep = Annotated[
    WorkspaceMemberRepository,
    Depends(get_workspace_member_repository)
]


def get_workspace_service(
    session: SessionDep,
    workspace_repo: WorkSpaceRepositoryDep,
    members_repo: WorkSpaceMemberRepositoryDep,
    user_repo: UserRepositoryDep,
) -> WorkspaceService:
    return WorkspaceService(
        session=session,
        workspaces_repo=workspace_repo,
        members_repo=members_repo,
        users_repo=user_repo,
    )

WorkspaceServiceDep = Annotated[
    WorkspaceService,
    Depends(get_workspace_service),
]


async def get_current_workspace(
    workspace_id: int,
    current_user: CurrentUserDep,
    workspace_repo: WorkSpaceRepositoryDep,
    members_repo: WorkSpaceMemberRepositoryDep,
) -> WorkSpace:
    workspace = await workspace_repo.get_by_id(workspace_id)

    if not workspace:
        raise WorkspaceNotFoundError()

    member = await members_repo.get_member(
        workspace_id,
        current_user.id
    )

    if not member:
        raise WorkspaceMemberNotFoundError()

    return workspace

CurrentWorkSpaceDep = Annotated[
    WorkSpace,
    Depends(get_current_workspace),
]


async def get_current_workspace_member(
    workspace_id: int,
    current_user: CurrentUserDep,
    member_repository: WorkSpaceMemberRepositoryDep,
) -> WorkSpaceMember:
    member = await member_repository.get_member(
        workspace_id,
        current_user.id,
    )

    if member is None:
        raise WorkspaceMemberNotFoundError()

    return member

CurrentWorkspaceMemberDep = Annotated[
    WorkSpaceMember,
    Depends(get_current_workspace_member),
]


async def require_workspace_admin(
    member: CurrentWorkspaceMemberDep,
) -> WorkSpaceMember:
    if member.role not in {
        WorkspaceRole.OWNER,
        WorkspaceRole.ADMIN,
    }:
        raise WorkspacePermissionDeniedError()

    return member

WorkspaceAdminDep = Annotated[
    WorkSpaceMember,
    Depends(require_workspace_admin),
]


async def require_workspace_owner(
    member: CurrentWorkspaceMemberDep,
) -> WorkSpaceMember:
    if member.role != WorkspaceRole.OWNER:
        raise WorkspacePermissionDeniedError()

    return member

WorkspaceOwnerDep = Annotated[
    WorkSpaceMember,
    Depends(require_workspace_owner),
]


async def get_target_workspace_member(
    workspace_id: int,
    user_id: int,
    member_repository: WorkSpaceMemberRepositoryDep,
) -> WorkSpaceMember:
    member = await member_repository.get_member(
        workspace_id,
        user_id,
    )

    if member is None:
        raise WorkspaceMemberNotFoundError()

    return member

TargetWorkspaceMemberDep = Annotated[
    WorkSpaceMember,
    Depends(get_target_workspace_member),
]