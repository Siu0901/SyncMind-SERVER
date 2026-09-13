import pytest

from app.domains.workspace.dependencies import (
    get_current_workspace,
    get_current_workspace_member,
    get_target_workspace_member,
    get_workspace_service,
    require_workspace_admin,
    require_workspace_owner,
)
from app.domains.workspace.enums import WorkspaceRole
from app.domains.workspace.exceptions import (
    WorkspaceMemberNotFoundError,
    WorkspaceNotFoundError,
    WorkspaceOwnerRequiredError,
    WorkspacePermissionDeniedError,
)
from app.domains.workspace.service import WorkspaceService

pytestmark = pytest.mark.anyio


# 워크스페이스 접근 (존재 + 소속)
class TestGetCurrentWorkspace:
    async def test_workspace_not_found(
        self, mock_workspace_repo, mock_member_repo, make_user
    ):
        mock_workspace_repo.get_by_id.return_value = None

        with pytest.raises(WorkspaceNotFoundError):
            await get_current_workspace(
                workspace_id=99,
                current_user=make_user(),
                workspace_repo=mock_workspace_repo,
                members_repo=mock_member_repo,
            )

        # 존재하지 않으면 멤버 조회까지 가지 않는다
        mock_member_repo.get_member.assert_not_awaited()

    async def test_not_a_member(
        self, mock_workspace_repo, mock_member_repo, make_user, make_workspace
    ):
        # 남의 워크스페이스는 존재 여부를 알려주지 않고 404 로 막는다
        mock_workspace_repo.get_by_id.return_value = make_workspace(workspace_id=1)
        mock_member_repo.get_member.return_value = None

        with pytest.raises(WorkspaceMemberNotFoundError):
            await get_current_workspace(
                workspace_id=1,
                current_user=make_user(user_id=7),
                workspace_repo=mock_workspace_repo,
                members_repo=mock_member_repo,
            )

    async def test_success(
        self, mock_workspace_repo, mock_member_repo, make_user, make_workspace,
        make_member,
    ):
        workspace = make_workspace(workspace_id=1)
        mock_workspace_repo.get_by_id.return_value = workspace
        mock_member_repo.get_member.return_value = make_member(user_id=7)

        result = await get_current_workspace(
            workspace_id=1,
            current_user=make_user(user_id=7),
            workspace_repo=mock_workspace_repo,
            members_repo=mock_member_repo,
        )

        assert result is workspace
        mock_member_repo.get_member.assert_awaited_once_with(1, 7)


# 내 멤버십
class TestGetCurrentWorkspaceMember:
    async def test_not_a_member(self, mock_member_repo, make_user):
        mock_member_repo.get_member.return_value = None

        with pytest.raises(WorkspaceMemberNotFoundError):
            await get_current_workspace_member(
                workspace_id=1,
                current_user=make_user(user_id=7),
                member_repository=mock_member_repo,
            )

    async def test_success(self, mock_member_repo, make_user, make_member):
        member = make_member(user_id=7, role=WorkspaceRole.ADMIN)
        mock_member_repo.get_member.return_value = member

        result = await get_current_workspace_member(
            workspace_id=1,
            current_user=make_user(user_id=7),
            member_repository=mock_member_repo,
        )

        assert result is member


# 권한 가드
class TestRoleGuards:
    @pytest.mark.parametrize("role", [WorkspaceRole.OWNER, WorkspaceRole.ADMIN])
    async def test_admin_guard_allows(self, make_member, role):
        member = make_member(role=role)

        assert await require_workspace_admin(member) is member

    async def test_admin_guard_blocks_member(self, make_member):
        with pytest.raises(WorkspacePermissionDeniedError):
            await require_workspace_admin(make_member(role=WorkspaceRole.MEMBER))

    async def test_owner_guard_allows_owner(self, make_member):
        member = make_member(role=WorkspaceRole.OWNER)

        assert await require_workspace_owner(member) is member

    @pytest.mark.parametrize("role", [WorkspaceRole.ADMIN, WorkspaceRole.MEMBER])
    async def test_owner_guard_blocks_others(self, make_member, role):
        # owner 가드는 admin 가드와 구분되는 전용 예외를 쓴다
        with pytest.raises(WorkspaceOwnerRequiredError):
            await require_workspace_owner(make_member(role=role))


# 조작 대상 멤버
class TestGetTargetWorkspaceMember:
    async def test_not_found(self, mock_member_repo):
        mock_member_repo.get_member.return_value = None

        with pytest.raises(WorkspaceMemberNotFoundError):
            await get_target_workspace_member(
                workspace_id=1, member_id=999, member_repository=mock_member_repo
            )

    async def test_success(self, mock_member_repo, make_member):
        target = make_member(user_id=9)
        mock_member_repo.get_member.return_value = target

        result = await get_target_workspace_member(
            workspace_id=1, member_id=9, member_repository=mock_member_repo
        )

        assert result is target
        # 다른 워크스페이스의 멤버를 건드리지 못하도록 workspace_id 로 스코프가 걸린다
        mock_member_repo.get_member.assert_awaited_once_with(1, 9)


# 서비스 배선
class TestServiceFactory:
    def test_get_workspace_service_wiring(
        self, mock_session, mock_workspace_repo, mock_member_repo, mock_user_repo
    ):
        service = get_workspace_service(
            session=mock_session,
            workspace_repo=mock_workspace_repo,
            members_repo=mock_member_repo,
            user_repo=mock_user_repo,
        )

        assert isinstance(service, WorkspaceService)
        assert service.session is mock_session
        assert service.workspaces_repo is mock_workspace_repo
        assert service.members_repo is mock_member_repo
        assert service.users_repo is mock_user_repo
