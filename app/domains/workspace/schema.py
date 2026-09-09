from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domains.workspace.enums import WorkspaceRole


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
    )


class WorkspaceUpdateRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
    )


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    name: str
    created_at: datetime
    updated_at: datetime


class WorkspaceMemberResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    user_id: int
    role: WorkspaceRole
    joined_at: datetime


class WorkspaceMemberAddRequest(BaseModel):
    user_id: int
    role: WorkspaceRole = WorkspaceRole.MEMBER


class WorkspaceMemberRoleUpdateRequest(BaseModel):
    role: WorkspaceRole