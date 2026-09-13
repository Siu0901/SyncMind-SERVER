"""
app/domains/workspace/router.py - workspace API 통합 테스트.

서비스는 mock 으로 대체하지만, 권한 의존성
(get_current_workspace / require_workspace_admin / require_workspace_owner)은
실제로 통과시켜서 역할별 접근 제어를 HTTP 레벨에서 검증한다.
인증(get_current_user)은 auth 테스트에서 이미 다뤘으므로 여기서는 override 로 건너뛴다.
"""

from unittest.mock import AsyncMock

import pytest

from app.core.database import get_session
from app.domains.auth.dependencies import get_current_user
from app.domains.workspace.dependencies import (
    get_workspace_member_repository,
    get_workspace_repository,
    get_workspace_service,
)
from app.domains.workspace.enums import WorkspaceRole
from app.domains.workspace.service import WorkspaceService

pytestmark = pytest.mark.anyio


@pytest.fixture
def fake_workspace_service(override_dependency) -> AsyncMock:
    service = AsyncMock(spec=WorkspaceService)
    override_dependency(get_workspace_service, lambda: service)
    return service


@pytest.fixture
def current_user(
    override_dependency, mock_session, mock_workspace_repo, mock_member_repo, make_user
):
    user = make_user(user_id=7)

    override_dependency(get_current_user, lambda: user)
    override_dependency(get_session, lambda: mock_session)
    override_dependency(get_workspace_repository, lambda: mock_workspace_repo)
    override_dependency(get_workspace_member_repository, lambda: mock_member_repo)

    return user


@pytest.fixture
def join_workspace(mock_workspace_repo, member_lookup, make_workspace, make_member):
    """워크스페이스 1번에 user 7 이 지정한 역할로 소속돼 있는 상태를 만든다."""

    def _join(role: WorkspaceRole = WorkspaceRole.MEMBER, *, others: dict = None):
        workspace = make_workspace(workspace_id=1)
        mock_workspace_repo.get_by_id.return_value = workspace

        members = {7: make_member(user_id=7, role=role)}
        members.update(others or {})
        member_lookup(members)

        return workspace

    return _join


# POST /workspaces/create
class TestCreateWorkspace:
    async def test_success(
        self, client, current_user, fake_workspace_service, make_workspace
    ):
        fake_workspace_service.create_workspace.return_value = make_workspace(
            workspace_id=1, name="새 워크스페이스"
        )

        response = await client.post(
            "/workspaces/create", json={"name": "새 워크스페이스"}
        )

        assert response.status_code == 200
        assert response.json()["id"] == 1
        assert response.json()["name"] == "새 워크스페이스"

        args = fake_workspace_service.create_workspace.await_args.args
        assert args[0] is current_user
        assert args[1].name == "새 워크스페이스"

    @pytest.mark.parametrize("name", ["", "x" * 101])
    async def test_invalid_name(
        self, client, current_user, fake_workspace_service, name
    ):
        response = await client.post("/workspaces/create", json={"name": name})

        assert response.status_code == 422
        fake_workspace_service.create_workspace.assert_not_awaited()


# GET /workspaces/list
class TestListWorkspaces:
    async def test_success(
        self, client, current_user, fake_workspace_service, make_workspace
    ):
        fake_workspace_service.get_my_workspace.return_value = [
            make_workspace(1, "A"),
            make_workspace(2, "B"),
        ]

        response = await client.get("/workspaces/list")

        assert response.status_code == 200
        assert [w["name"] for w in response.json()] == ["A", "B"]

    async def test_empty(self, client, current_user, fake_workspace_service):
        fake_workspace_service.get_my_workspace.return_value = []

        response = await client.get("/workspaces/list")

        assert response.status_code == 200
        assert response.json() == []


# GET /workspaces/{workspace_id}
class TestGetWorkspace:
    async def test_success(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        workspace = join_workspace(WorkspaceRole.MEMBER)
        fake_workspace_service.get_workspace.return_value = workspace

        response = await client.get("/workspaces/1")

        assert response.status_code == 200
        assert response.json()["id"] == 1

    async def test_not_found(
        self, client, current_user, fake_workspace_service, mock_workspace_repo
    ):
        mock_workspace_repo.get_by_id.return_value = None

        response = await client.get("/workspaces/99")

        assert response.status_code == 404
        assert response.json() == {"detail": "Workspace not found"}

    async def test_not_a_member(
        self, client, current_user, fake_workspace_service, mock_workspace_repo,
        mock_member_repo, make_workspace,
    ):
        mock_workspace_repo.get_by_id.return_value = make_workspace(workspace_id=1)
        mock_member_repo.get_member.return_value = None

        response = await client.get("/workspaces/1")

        assert response.status_code == 404
        assert response.json() == {"detail": "Workspace member not found"}

    async def test_non_numeric_id(self, client, current_user, fake_workspace_service):
        response = await client.get("/workspaces/abc")

        assert response.status_code == 422


# PATCH /workspaces/{workspace_id}
class TestUpdateWorkspace:
    @pytest.mark.parametrize("role", [WorkspaceRole.OWNER, WorkspaceRole.ADMIN])
    async def test_admin_can_update(
        self, client, current_user, fake_workspace_service, join_workspace,
        make_workspace, role,
    ):
        join_workspace(role)
        fake_workspace_service.update_workspace.return_value = make_workspace(
            1, "수정된 이름"
        )

        response = await client.patch("/workspaces/1", json={"name": "수정된 이름"})

        assert response.status_code == 200
        assert response.json()["name"] == "수정된 이름"

    async def test_member_cannot_update(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        join_workspace(WorkspaceRole.MEMBER)

        response = await client.patch("/workspaces/1", json={"name": "수정된 이름"})

        assert response.status_code == 403
        assert response.json() == {"detail": "Workspace permission denied"}
        fake_workspace_service.update_workspace.assert_not_awaited()

    async def test_invalid_name(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        join_workspace(WorkspaceRole.OWNER)

        response = await client.patch("/workspaces/1", json={"name": ""})

        assert response.status_code == 422


# DELETE /workspaces/{workspace_id}
class TestDeleteWorkspace:
    async def test_owner_can_delete(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        join_workspace(WorkspaceRole.OWNER)

        response = await client.delete("/workspaces/1")

        assert response.status_code == 200
        assert response.json() == {"message": "Successfully deleted workspace"}
        fake_workspace_service.delete_workspace.assert_awaited_once()

    @pytest.mark.parametrize("role", [WorkspaceRole.ADMIN, WorkspaceRole.MEMBER])
    async def test_non_owner_cannot_delete(
        self, client, current_user, fake_workspace_service, join_workspace, role
    ):
        join_workspace(role)

        response = await client.delete("/workspaces/1")

        assert response.status_code == 403
        fake_workspace_service.delete_workspace.assert_not_awaited()


# GET /workspaces/{workspace_id}/members
class TestListMembers:
    async def test_success(
        self, client, current_user, fake_workspace_service, join_workspace, make_member
    ):
        join_workspace(WorkspaceRole.MEMBER)
        fake_workspace_service.get_member.return_value = [
            make_member(user_id=7, role=WorkspaceRole.OWNER),
            make_member(user_id=9, role=WorkspaceRole.MEMBER),
        ]

        response = await client.get("/workspaces/1/members")

        assert response.status_code == 200

        body = response.json()
        assert [m["user_id"] for m in body] == [7, 9]
        assert body[0]["role"] == "owner"


# POST /workspaces/{workspace_id}/members
class TestAddMember:
    @pytest.mark.parametrize("role", [WorkspaceRole.OWNER, WorkspaceRole.ADMIN])
    async def test_admin_can_add(
        self, client, current_user, fake_workspace_service, join_workspace,
        make_member, role,
    ):
        join_workspace(role)
        fake_workspace_service.add_member.return_value = make_member(
            user_id=9, role=WorkspaceRole.MEMBER
        )

        response = await client.post(
            "/workspaces/1/members", json={"user_id": 9, "role": "member"}
        )

        assert response.status_code == 200
        assert response.json()["user_id"] == 9

        args = fake_workspace_service.add_member.await_args.args
        assert args[0] == 1          # 경로의 workspace_id 를 서비스로 넘긴다
        assert args[1].user_id == 9

    async def test_member_cannot_add(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        join_workspace(WorkspaceRole.MEMBER)

        response = await client.post("/workspaces/1/members", json={"user_id": 9})

        assert response.status_code == 403
        fake_workspace_service.add_member.assert_not_awaited()

    async def test_role_defaults_to_member(
        self, client, current_user, fake_workspace_service, join_workspace, make_member
    ):
        join_workspace(WorkspaceRole.OWNER)
        fake_workspace_service.add_member.return_value = make_member(user_id=9)

        response = await client.post("/workspaces/1/members", json={"user_id": 9})

        assert response.status_code == 200
        assert (
            fake_workspace_service.add_member.await_args.args[1].role
            == WorkspaceRole.MEMBER
        )

    async def test_invalid_role(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        join_workspace(WorkspaceRole.OWNER)

        response = await client.post(
            "/workspaces/1/members", json={"user_id": 9, "role": "superuser"}
        )

        assert response.status_code == 422


# PATCH /workspaces/{workspace_id}/members/{member_id}
class TestUpdateMemberRole:
    async def test_owner_can_update_role(
        self, client, current_user, fake_workspace_service, join_workspace,
        make_member,
    ):
        join_workspace(
            WorkspaceRole.OWNER,
            others={9: make_member(user_id=9, role=WorkspaceRole.MEMBER)},
        )
        fake_workspace_service.update_member_role.return_value = make_member(
            user_id=9, role=WorkspaceRole.ADMIN
        )

        response = await client.patch(
            "/workspaces/1/members/9", json={"role": "admin"}
        )

        assert response.status_code == 200
        assert response.json()["role"] == "admin"

    async def test_target_comes_from_path(
        self, client, current_user, fake_workspace_service, join_workspace,
        mock_member_repo, make_member,
    ):
        # 대상은 경로의 member_id 로만 결정된다 (쿼리 파라미터로 바꿔치기 불가)
        join_workspace(
            WorkspaceRole.OWNER,
            others={9: make_member(user_id=9, role=WorkspaceRole.MEMBER)},
        )
        fake_workspace_service.update_member_role.return_value = make_member(user_id=9)

        response = await client.patch(
            "/workspaces/1/members/9",
            params={"user_id": 99999},
            json={"role": "admin"},
        )

        assert response.status_code == 200

        looked_up = [call.args[1] for call in mock_member_repo.get_member.await_args_list]
        assert 9 in looked_up
        assert 99999 not in looked_up

    async def test_non_numeric_member_id(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        join_workspace(WorkspaceRole.OWNER)

        response = await client.patch(
            "/workspaces/1/members/abc", json={"role": "admin"}
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("role", [WorkspaceRole.ADMIN, WorkspaceRole.MEMBER])
    async def test_non_owner_cannot_update_role(
        self, client, current_user, fake_workspace_service, join_workspace,
        make_member, role,
    ):
        join_workspace(
            role, others={9: make_member(user_id=9, role=WorkspaceRole.MEMBER)}
        )

        response = await client.patch(
            "/workspaces/1/members/9", json={"role": "admin"}
        )

        assert response.status_code == 403
        assert response.json() == {"detail": "Workspace owner permission required"}
        fake_workspace_service.update_member_role.assert_not_awaited()

    async def test_target_not_found(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        join_workspace(WorkspaceRole.OWNER)

        response = await client.patch(
            "/workspaces/1/members/999", json={"role": "admin"}
        )

        assert response.status_code == 404
        assert response.json() == {"detail": "Workspace member not found"}


# DELETE /workspaces/{workspace_id}/members/{member_id}
class TestRemoveMember:
    @pytest.mark.parametrize("role", [WorkspaceRole.OWNER, WorkspaceRole.ADMIN])
    async def test_admin_can_remove(
        self, client, current_user, fake_workspace_service, join_workspace,
        make_member, role,
    ):
        join_workspace(
            role, others={9: make_member(user_id=9, role=WorkspaceRole.MEMBER)}
        )

        response = await client.delete("/workspaces/1/members/9")

        assert response.status_code == 200
        assert response.json() == {"message": "Successfully deleted workspace member"}

        args = fake_workspace_service.remove_member.await_args.args
        assert args[0].user_id == 7      # requester
        assert args[1].user_id == 9      # target

    async def test_member_cannot_remove(
        self, client, current_user, fake_workspace_service, join_workspace,
        make_member,
    ):
        join_workspace(
            WorkspaceRole.MEMBER,
            others={9: make_member(user_id=9, role=WorkspaceRole.MEMBER)},
        )

        response = await client.delete("/workspaces/1/members/9")

        assert response.status_code == 403
        fake_workspace_service.remove_member.assert_not_awaited()

    async def test_target_not_found(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        join_workspace(WorkspaceRole.OWNER)

        response = await client.delete("/workspaces/1/members/999")

        assert response.status_code == 404


# DELETE /workspaces/{workspace_id}/members/me
class TestLeaveWorkspace:
    @pytest.mark.parametrize("role", [WorkspaceRole.MEMBER, WorkspaceRole.ADMIN])
    async def test_any_member_can_leave(
        self, client, current_user, fake_workspace_service, join_workspace, role
    ):
        # 등급 가드가 없으므로 일반 멤버도 나갈 수 있다 (원래 403 나던 케이스)
        join_workspace(role)

        response = await client.delete("/workspaces/1/members/me")

        assert response.status_code == 200
        assert response.json() == {"message": "Successfully left workspace"}

        # 서비스에는 로그인 사용자의 멤버십 행 하나만 전달된다
        args = fake_workspace_service.leave_workspace.await_args.args
        assert len(args) == 1
        assert args[0].user_id == 7

    async def test_non_member_gets_404(
        self, client, current_user, fake_workspace_service, mock_member_repo
    ):
        """
        비멤버는 404 로 끊긴다.

        예전 /myself/{member_id} 구조에서는 로그인만 한 외부인이 member_id 를 바꿔가며
        403(멤버) / 400(오너) / 404(비멤버) 응답 차이로 남의 워크스페이스 구성원을
        알아낼 수 있었다. 그 유출 경로를 막았는지 확인하는 회귀 테스트.
        """
        mock_member_repo.get_member.return_value = None

        response = await client.delete("/workspaces/1/members/me")

        assert response.status_code == 404
        assert response.json() == {"detail": "Workspace member not found"}
        fake_workspace_service.leave_workspace.assert_not_awaited()

    async def test_owner_cannot_leave(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        from app.domains.workspace.exceptions import CannotRemoveWorkspaceOwnerError

        join_workspace(WorkspaceRole.OWNER)
        fake_workspace_service.leave_workspace.side_effect = (
            CannotRemoveWorkspaceOwnerError()
        )

        response = await client.delete("/workspaces/1/members/me")

        assert response.status_code == 400
        assert response.json() == {"detail": "Workspace owner cannot be removed"}

    async def test_me_is_not_parsed_as_member_id(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        # /members/me 가 /members/{member_id} 보다 먼저 선언돼 있어야 한다.
        # 순서가 뒤바뀌면 "me" 를 int 로 파싱하려다 422 가 난다.
        join_workspace(WorkspaceRole.MEMBER)

        response = await client.delete("/workspaces/1/members/me")

        assert response.status_code == 200
        fake_workspace_service.remove_member.assert_not_awaited()

    async def test_cannot_target_others(
        self, client, current_user, fake_workspace_service, join_workspace
    ):
        # 탈퇴 경로에는 대상을 지정할 파라미터가 없다.
        # 쿼리로 끼워넣어도 무시되고 항상 본인만 처리된다.
        join_workspace(WorkspaceRole.MEMBER)

        response = await client.delete(
            "/workspaces/1/members/me", params={"member_id": 9, "user_id": 9}
        )

        assert response.status_code == 200
        assert fake_workspace_service.leave_workspace.await_args.args[0].user_id == 7

    async def test_old_myself_route_is_gone(
        self, client, current_user, fake_workspace_service
    ):
        response = await client.delete("/workspaces/1/myself/7")

        assert response.status_code == 404
