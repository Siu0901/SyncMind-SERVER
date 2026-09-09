from fastapi import APIRouter, status

from app.domains.auth.dependencies import (
    CurrentUserDep,
)
from app.domains.workspace.dependencies import (
    CurrentWorkSpaceDep,
    TargetWorkspaceMemberDep,
    WorkspaceAdminDep,
    WorkspaceOwnerDep,
    WorkspaceServiceDep,
)
from app.domains.workspace.schema import (
    WorkspaceCreateRequest,
    WorkspaceMemberAddRequest,
    WorkspaceMemberResponse,
    WorkspaceMemberRoleUpdateRequest,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)


workspace_router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
)


@workspace_router.post(
    "/create",
    response_model=WorkspaceResponse
)
async def create_workspace(
    data: WorkspaceCreateRequest,
    user: CurrentUserDep,
    service: WorkspaceServiceDep,
):
    return await service.create_workspace(user, data)


@workspace_router.get(
    "/list",
    response_model=list[WorkspaceResponse],
)
async def get_workspaces(
    user: CurrentUserDep,
    service: WorkspaceServiceDep,
):
    return await service.get_my_workspace(user)


@workspace_router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
)
async def get_workspace(
    workspace: CurrentWorkSpaceDep,
    service: WorkspaceServiceDep,
):
    return await service.get_workspace(workspace)


@workspace_router.patch(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
)
async def update_workspace(
    workspace: CurrentWorkSpaceDep,
    _: WorkspaceAdminDep,
    data: WorkspaceUpdateRequest,
    service: WorkspaceServiceDep,
):
    return await service.update_workspace(workspace, data)


@workspace_router.delete("/{workspace_id}")
async def delete_workspace(
    workspace: CurrentWorkSpaceDep,
    _: WorkspaceOwnerDep,
    service: WorkspaceServiceDep,
):
    await service.delete_workspace(workspace)
    return {"message": "Successfully deleted workspace"}


@workspace_router.get(
    "/{workspace_id}/members",
    response_model=list[WorkspaceMemberResponse]
)
async def get_workspace_members(
    workspace: CurrentWorkSpaceDep,
    service: WorkspaceServiceDep,
):
    return await service.get_member(workspace)


@workspace_router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberResponse,
)
async def add_workspace_member(
    workspace: CurrentWorkSpaceDep,
    _: WorkspaceAdminDep,
    data: WorkspaceMemberAddRequest,
    service: WorkspaceServiceDep,
):
    return await service.add_member(workspace.id, data)


@workspace_router.patch(
    "/{workspace_id}/members/{member_id}",
    response_model=WorkspaceMemberResponse,
)
async def update_workspace_member(
    _: WorkspaceOwnerDep,
    member: TargetWorkspaceMemberDep,
    data: WorkspaceMemberRoleUpdateRequest,
    service: WorkspaceServiceDep,
):
    return await service.update_member_role(member, data)


@workspace_router.delete("/{workspace_id}/members/{member_id}")
async def delete_workspace_member(
    requester: WorkspaceAdminDep,
    member: TargetWorkspaceMemberDep,
    service: WorkspaceServiceDep,
):
    await service.remove_member(requester, member)
    return {"message": "Successfully deleted workspace member"}