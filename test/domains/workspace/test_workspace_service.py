import pytest

from app.domains.auth.exceptions import UserNotFoundError
from app.domains.workspace.enums import WorkspaceRole
from app.domains.workspace.exceptions import (
    CannotChangeWorkspaceOwnerRoleError,
    CannotRemoveWorkspaceOwnerError,
    WorkspaceMemberAlreadyExistsError,
    WorkspacePermissionDeniedError,
)
from app.domains.workspace.model import WorkSpace, WorkSpaceMember
from app.domains.workspace.schema import (
    WorkspaceCreateRequest,
    WorkspaceMemberAddRequest,
    WorkspaceMemberRoleUpdateRequest,
    WorkspaceUpdateRequest,
)

pytestmark = pytest.mark.anyio


# 워크스페이스 생성
class TestCreateWorkspace:
    async def test_creates_workspace_with_owner(
        self, workspace_service, mock_workspace_repo, mock_member_repo,
        mock_session, make_user,
    ):
        user = make_user(user_id=7)

        workspace = await workspace_service.create_workspace(
            user, WorkspaceCreateRequest(name="내 워크스페이스")
        )

        created: WorkSpace = mock_workspace_repo.create.await_args.args[0]
        assert created.name == "내 워크스페이스"
        assert workspace is created

        # 생성자는 자동으로 OWNER 멤버가 된다
        owner: WorkSpaceMember = mock_member_repo.create.await_args.args[0]
        assert owner.workspace_id == created.id
        assert owner.user_id == 7
        assert owner.role == WorkspaceRole.OWNER

        # 워크스페이스 + 오너 멤버가 한 트랜잭션으로 묶여야 한다
        mock_session.commit.assert_awaited_once()

    async def test_name_is_stripped(
        self, workspace_service, mock_workspace_repo, make_user
    ):
        await workspace_service.create_workspace(
            make_user(), WorkspaceCreateRequest(name="  공백 워크스페이스  ")
        )

        assert mock_workspace_repo.create.await_args.args[0].name == "공백 워크스페이스"


# 목록 / 단건 조회
class TestGetWorkspace:
    async def test_get_my_workspace(
        self, workspace_service, mock_workspace_repo, make_user, make_workspace
    ):
        expected = [make_workspace(1), make_workspace(2)]
        mock_workspace_repo.get_by_user_id.return_value = expected

        result = await workspace_service.get_my_workspace(make_user(user_id=7))

        assert result == expected
        mock_workspace_repo.get_by_user_id.assert_awaited_once_with(7)

    async def test_get_my_workspace_empty(
        self, workspace_service, mock_workspace_repo, make_user
    ):
        mock_workspace_repo.get_by_user_id.return_value = []

        assert await workspace_service.get_my_workspace(make_user()) == []

    async def test_get_workspace_returns_as_is(self, workspace_service, make_workspace):
        # 접근 권한 확인은 이미 의존성(get_current_workspace)에서 끝났으므로 그대로 돌려준다
        workspace = make_workspace()

        assert await workspace_service.get_workspace(workspace) is workspace


# 수정 / 삭제
class TestUpdateAndDeleteWorkspace:
    async def test_update_name(
        self, workspace_service, mock_session, make_workspace
    ):
        workspace = make_workspace(name="옛이름")

        result = await workspace_service.update_workspace(
            workspace, WorkspaceUpdateRequest(name="  새이름  ")
        )

        assert result.name == "새이름"
        mock_session.add.assert_called_once_with(workspace)
        mock_session.commit.assert_awaited_once()
        mock_session.refresh.assert_awaited_once_with(workspace)

    async def test_delete(
        self, workspace_service, mock_workspace_repo, mock_session, make_workspace
    ):
        workspace = make_workspace(workspace_id=3)

        await workspace_service.delete_workspace(workspace)

        mock_workspace_repo.delete.assert_awaited_once_with(workspace)
        mock_session.commit.assert_awaited_once()


# 멤버 조회
class TestGetMembers:
    async def test_get_member(
        self, workspace_service, mock_member_repo, make_workspace, make_member
    ):
        expected = [make_member(user_id=1), make_member(user_id=2)]
        mock_member_repo.get_all_members.return_value = expected

        result = await workspace_service.get_member(make_workspace(workspace_id=5))

        assert result == expected
        mock_member_repo.get_all_members.assert_awaited_once_with(5)


# 멤버 추가
class TestAddMember:
    async def test_user_not_found(
        self, workspace_service, mock_user_repo, mock_member_repo, mock_session
    ):
        mock_user_repo.get_by_id.return_value = None

        with pytest.raises(UserNotFoundError):
            await workspace_service.add_member(
                1, WorkspaceMemberAddRequest(user_id=999)
            )

        mock_member_repo.create.assert_not_awaited()
        mock_session.commit.assert_not_awaited()

    async def test_already_a_member(
        self, workspace_service, mock_user_repo, mock_member_repo, mock_session,
        make_user, make_member,
    ):
        mock_user_repo.get_by_id.return_value = make_user(user_id=2)
        mock_member_repo.get_member.return_value = make_member(user_id=2)

        with pytest.raises(WorkspaceMemberAlreadyExistsError):
            await workspace_service.add_member(1, WorkspaceMemberAddRequest(user_id=2))

        mock_member_repo.create.assert_not_awaited()
        mock_session.commit.assert_not_awaited()

    async def test_success_defaults_to_member_role(
        self, workspace_service, mock_user_repo, mock_member_repo, mock_session,
        make_user,
    ):
        mock_user_repo.get_by_id.return_value = make_user(user_id=2)
        mock_member_repo.get_member.return_value = None

        member = await workspace_service.add_member(
            1, WorkspaceMemberAddRequest(user_id=2)
        )

        assert member.workspace_id == 1
        assert member.user_id == 2
        assert member.role == WorkspaceRole.MEMBER
        mock_session.commit.assert_awaited_once()

    async def test_success_with_admin_role(
        self, workspace_service, mock_user_repo, mock_member_repo, make_user
    ):
        mock_user_repo.get_by_id.return_value = make_user(user_id=2)
        mock_member_repo.get_member.return_value = None

        member = await workspace_service.add_member(
            1, WorkspaceMemberAddRequest(user_id=2, role=WorkspaceRole.ADMIN)
        )

        assert member.role == WorkspaceRole.ADMIN

    async def test_cannot_add_as_owner(
        self, workspace_service, mock_user_repo, mock_member_repo, mock_session,
        make_user,
    ):
        # 초대로 OWNER 를 만들 수 없다. (오너는 워크스페이스 생성자 1명뿐)
        mock_user_repo.get_by_id.return_value = make_user(user_id=2)
        mock_member_repo.get_member.return_value = None

        with pytest.raises(WorkspacePermissionDeniedError):
            await workspace_service.add_member(
                1, WorkspaceMemberAddRequest(user_id=2, role=WorkspaceRole.OWNER)
            )

        mock_member_repo.create.assert_not_awaited()
        mock_session.commit.assert_not_awaited()


# 멤버 역할 변경
class TestUpdateMemberRole:
    async def test_owner_role_cannot_be_changed(
        self, workspace_service, mock_session, make_member
    ):
        owner = make_member(user_id=1, role=WorkspaceRole.OWNER)

        with pytest.raises(CannotChangeWorkspaceOwnerRoleError):
            await workspace_service.update_member_role(
                owner, WorkspaceMemberRoleUpdateRequest(role=WorkspaceRole.ADMIN)
            )

        mock_session.commit.assert_not_awaited()

    async def test_cannot_promote_to_owner(
        self, workspace_service, mock_session, make_member
    ):
        # 소유권 이전은 지원하지 않는다
        member = make_member(user_id=2, role=WorkspaceRole.MEMBER)

        with pytest.raises(CannotChangeWorkspaceOwnerRoleError):
            await workspace_service.update_member_role(
                member, WorkspaceMemberRoleUpdateRequest(role=WorkspaceRole.OWNER)
            )

        mock_session.commit.assert_not_awaited()

    @pytest.mark.parametrize(
        "before, after",
        [
            (WorkspaceRole.MEMBER, WorkspaceRole.ADMIN),
            (WorkspaceRole.ADMIN, WorkspaceRole.MEMBER),
            (WorkspaceRole.MEMBER, WorkspaceRole.MEMBER),
        ],
    )
    async def test_success(
        self, workspace_service, mock_session, make_member, before, after
    ):
        member = make_member(user_id=2, role=before)

        result = await workspace_service.update_member_role(
            member, WorkspaceMemberRoleUpdateRequest(role=after)
        )

        assert result.role == after
        mock_session.add.assert_called_once_with(member)
        mock_session.commit.assert_awaited_once()
        mock_session.refresh.assert_awaited_once_with(member)


# 멤버 삭제
class TestRemoveMember:
    async def test_owner_cannot_be_removed(
        self, workspace_service, mock_member_repo, mock_session, make_member
    ):
        requester = make_member(user_id=1, role=WorkspaceRole.OWNER)
        target = make_member(user_id=1, role=WorkspaceRole.OWNER)

        with pytest.raises(CannotRemoveWorkspaceOwnerError):
            await workspace_service.remove_member(requester, target)

        mock_member_repo.delete.assert_not_awaited()
        mock_session.commit.assert_not_awaited()

    async def test_admin_cannot_remove_another_admin(
        self, workspace_service, mock_member_repo, make_member
    ):
        requester = make_member(user_id=2, role=WorkspaceRole.ADMIN)
        target = make_member(user_id=3, role=WorkspaceRole.ADMIN)

        with pytest.raises(WorkspacePermissionDeniedError):
            await workspace_service.remove_member(requester, target)

        mock_member_repo.delete.assert_not_awaited()

    async def test_admin_removing_self_is_blocked_here(
        self, workspace_service, mock_member_repo, make_member
    ):
        # 이 경로는 '남을 내보내기' 전용이라 관리자 본인도 동급 규칙에 걸린다.
        # 본인 탈퇴는 leave_workspace 를 쓴다 (TestLeaveWorkspace 참고).
        admin = make_member(user_id=2, role=WorkspaceRole.ADMIN)

        with pytest.raises(WorkspacePermissionDeniedError):
            await workspace_service.remove_member(admin, admin)

        mock_member_repo.delete.assert_not_awaited()

    @pytest.mark.parametrize(
        "requester_role, target_role",
        [
            (WorkspaceRole.OWNER, WorkspaceRole.ADMIN),
            (WorkspaceRole.OWNER, WorkspaceRole.MEMBER),
            (WorkspaceRole.ADMIN, WorkspaceRole.MEMBER),
        ],
    )
    async def test_success(
        self, workspace_service, mock_member_repo, mock_session, make_member,
        requester_role, target_role,
    ):
        requester = make_member(user_id=1, role=requester_role)
        target = make_member(user_id=9, role=target_role)

        await workspace_service.remove_member(requester, target)

        mock_member_repo.delete.assert_awaited_once_with(target)
        mock_session.commit.assert_awaited_once()


# 본인 탈퇴
class TestLeaveWorkspace:
    @pytest.mark.parametrize("role", [WorkspaceRole.MEMBER, WorkspaceRole.ADMIN])
    async def test_success(
        self, workspace_service, mock_member_repo, mock_session, make_member, role
    ):
        # 등급과 무관하게 본인은 나갈 수 있다
        member = make_member(user_id=2, role=role)

        await workspace_service.leave_workspace(member)

        mock_member_repo.delete.assert_awaited_once_with(member)
        mock_session.commit.assert_awaited_once()

    async def test_owner_cannot_leave(
        self, workspace_service, mock_member_repo, mock_session, make_member
    ):
        # 오너가 나가면 주인 없는 워크스페이스가 남는다 -> 삭제로 정리해야 한다
        owner = make_member(user_id=1, role=WorkspaceRole.OWNER)

        with pytest.raises(CannotRemoveWorkspaceOwnerError):
            await workspace_service.leave_workspace(owner)

        mock_member_repo.delete.assert_not_awaited()
        mock_session.commit.assert_not_awaited()

    async def test_takes_single_member_argument(self, workspace_service, make_member):
        # requester/target 두 개를 받지 않기 때문에 '남을 지정'하는 표현 자체가 없다.
        # (신원 비교 검사를 빼먹어도 우회가 생기지 않는 구조)
        import inspect

        params = inspect.signature(workspace_service.leave_workspace).parameters

        assert list(params) == ["member"]
