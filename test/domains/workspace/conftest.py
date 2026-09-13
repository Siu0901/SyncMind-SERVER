from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.domains.workspace.enums import WorkspaceRole
from app.domains.workspace.model import WorkSpace, WorkSpaceMember
from app.domains.workspace.repository import (
    WorkspaceMemberRepository,
    WorkspaceRepository,
)
from app.domains.workspace.service import WorkspaceService


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# 모델 팩토리
@pytest.fixture
def make_workspace():
    # created_at / updated_at 은 DB server_default 라 파이썬 객체에는 값이 없다.
    # WorkspaceResponse 검증에 필요하므로 채워서 넘긴다.
    def _make(workspace_id: int = 1, name: str = "테스트 워크스페이스") -> WorkSpace:
        workspace = WorkSpace(id=workspace_id, name=name)
        workspace.created_at = _NOW
        workspace.updated_at = _NOW
        return workspace

    return _make


@pytest.fixture
def make_member():
    def _make(
        workspace_id: int = 1,
        user_id: int = 1,
        role: WorkspaceRole = WorkspaceRole.MEMBER,
    ) -> WorkSpaceMember:
        member = WorkSpaceMember(
            workspace_id=workspace_id,
            user_id=user_id,
            role=role,
        )
        member.joined_at = _NOW
        return member

    return _make


# 리포지토리 mock
@pytest.fixture
def mock_workspace_repo() -> AsyncMock:
    repo = AsyncMock(spec=WorkspaceRepository)

    repo.get_by_id.return_value = None
    repo.get_by_user_id.return_value = []

    # 실제 create() 는 flush + refresh 로 PK 를 채운다. 그 효과만 흉내낸다.
    async def _create(workspace: WorkSpace):
        if workspace.id is None:
            workspace.id = 1

    repo.create.side_effect = _create

    return repo


@pytest.fixture
def mock_member_repo() -> AsyncMock:
    repo = AsyncMock(spec=WorkspaceMemberRepository)

    repo.get_member.return_value = None
    repo.get_all_members.return_value = []
    repo.create.side_effect = lambda member: member

    return repo


@pytest.fixture
def member_lookup(mock_member_repo):
    """
    get_member(workspace_id, user_id) 를 user_id 별로 다르게 응답시킨다.

    요청자(WorkspaceAdminDep) 와 대상(TargetWorkspaceMemberDep) 이
    같은 get_member 를 호출하기 때문에 둘을 구분하려면 이게 필요하다.
    """

    def _setup(members: dict[int, WorkSpaceMember | None]):
        async def _get_member(workspace_id: int, user_id: int):
            return members.get(user_id)

        mock_member_repo.get_member.side_effect = _get_member

    return _setup


@pytest.fixture
def workspace_service(
    mock_session, mock_workspace_repo, mock_member_repo, mock_user_repo
) -> WorkspaceService:
    return WorkspaceService(
        session=mock_session,
        workspaces_repo=mock_workspace_repo,
        members_repo=mock_member_repo,
        users_repo=mock_user_repo,
    )
