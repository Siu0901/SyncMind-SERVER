"""
WorkspaceRepository / WorkspaceMemberRepository 통합 테스트 (실제 PostgreSQL).

실행 방법은 test/integration/conftest.py 상단 참고.
TEST_DATABASE_URL 이 없으면 전부 skip 된다.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import text

from app.domains.user.model import User
from app.domains.user.repository import UserRepository
from app.domains.workspace.enums import WorkspaceRole
from app.domains.workspace.model import WorkSpace, WorkSpaceMember
from app.domains.workspace.repository import (
    WorkspaceMemberRepository,
    WorkspaceRepository,
)

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

_BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def new_user(email: str, name: str = "테스터") -> User:
    return User(email=email, password_hash="hash", name=name, is_active=True)


def new_workspace(name: str, *, created_offset_days: int = 0) -> WorkSpace:
    # server_default(now())는 같은 트랜잭션 안에서 전부 동일한 값이라
    # 정렬 검증이 불가능하다. 그래서 created_at 을 명시적으로 넣는다.
    workspace = WorkSpace(name=name)
    workspace.created_at = _BASE_TIME + timedelta(days=created_offset_days)
    workspace.updated_at = workspace.created_at
    return workspace


def new_member(
    workspace_id: int,
    user_id: int,
    role: WorkspaceRole = WorkspaceRole.MEMBER,
    *,
    joined_offset_minutes: int = 0,
) -> WorkSpaceMember:
    member = WorkSpaceMember(
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
    )
    member.joined_at = _BASE_TIME + timedelta(minutes=joined_offset_minutes)
    return member


# WorkspaceRepository
class TestWorkspaceRepository:
    async def test_create_assigns_id(self, db_session):
        repo = WorkspaceRepository(db_session)
        workspace = new_workspace("첫 워크스페이스")

        await repo.create(workspace)

        assert workspace.id is not None

    async def test_create_does_not_commit(self, db_session, db_engine):
        # create() 는 flush 만 한다. 커밋은 서비스(create_workspace)가 책임진다.
        repo = WorkspaceRepository(db_session)
        workspace = new_workspace("롤백될 워크스페이스")

        await repo.create(workspace)
        workspace_id = workspace.id

        await db_session.rollback()

        from sqlalchemy.ext.asyncio import async_sessionmaker
        from sqlmodel.ext.asyncio.session import AsyncSession

        factory = async_sessionmaker(bind=db_engine, class_=AsyncSession)

        async with factory() as other:
            assert await WorkspaceRepository(other).get_by_id(workspace_id) is None

    async def test_get_by_id(self, db_session):
        repo = WorkspaceRepository(db_session)
        workspace = new_workspace("조회 대상")
        await repo.create(workspace)
        await db_session.commit()

        found = await repo.get_by_id(workspace.id)

        assert found is not None
        assert found.name == "조회 대상"

    async def test_get_by_id_not_found(self, db_session):
        assert await WorkspaceRepository(db_session).get_by_id(999_999) is None

    async def test_get_by_user_id_returns_only_joined(self, db_session):
        """소속된 워크스페이스만 나와야 한다. 남의 워크스페이스가 섞이면 정보 유출."""
        user_repo = UserRepository(db_session)
        me = await user_repo.create(new_user("me@example.com"))
        other = await user_repo.create(new_user("other@example.com"))

        workspace_repo = WorkspaceRepository(db_session)
        member_repo = WorkspaceMemberRepository(db_session)

        mine = new_workspace("내 것")
        theirs = new_workspace("남의 것")
        await workspace_repo.create(mine)
        await workspace_repo.create(theirs)

        await member_repo.create(new_member(mine.id, me.id, WorkspaceRole.OWNER))
        await member_repo.create(new_member(theirs.id, other.id, WorkspaceRole.OWNER))
        await db_session.commit()

        result = await workspace_repo.get_by_user_id(me.id)

        assert [w.name for w in result] == ["내 것"]

    async def test_get_by_user_id_orders_by_created_at_desc(self, db_session):
        user = await UserRepository(db_session).create(new_user("order@example.com"))

        workspace_repo = WorkspaceRepository(db_session)
        member_repo = WorkspaceMemberRepository(db_session)

        for name, offset in [("오래된", 0), ("중간", 1), ("최신", 2)]:
            workspace = new_workspace(name, created_offset_days=offset)
            await workspace_repo.create(workspace)
            await member_repo.create(new_member(workspace.id, user.id))

        await db_session.commit()

        result = await workspace_repo.get_by_user_id(user.id)

        assert [w.name for w in result] == ["최신", "중간", "오래된"]

    async def test_get_by_user_id_empty(self, db_session):
        user = await UserRepository(db_session).create(new_user("solo@example.com"))

        assert await WorkspaceRepository(db_session).get_by_user_id(user.id) == []

    async def test_delete_cascades_to_members(self, db_session):
        """
        워크스페이스를 지우면 멤버 행도 같이 지워져야 한다.
        (ondelete="CASCADE" 가 실제 DDL 에 반영됐는지 확인)
        """
        user = await UserRepository(db_session).create(new_user("cascade@example.com"))

        workspace_repo = WorkspaceRepository(db_session)
        member_repo = WorkspaceMemberRepository(db_session)

        workspace = new_workspace("삭제될 워크스페이스")
        await workspace_repo.create(workspace)
        await member_repo.create(
            new_member(workspace.id, user.id, WorkspaceRole.OWNER)
        )
        await db_session.commit()

        workspace_id = workspace.id

        await workspace_repo.delete(workspace)
        await db_session.commit()

        assert await workspace_repo.get_by_id(workspace_id) is None
        assert await member_repo.get_all_members(workspace_id) == []

    async def test_deleting_user_cascades_to_membership(self, db_session):
        """유저 삭제 시에도 멤버 행이 남지 않아야 한다 (user_id FK CASCADE)."""
        user = await UserRepository(db_session).create(new_user("byebye@example.com"))

        workspace_repo = WorkspaceRepository(db_session)
        member_repo = WorkspaceMemberRepository(db_session)

        workspace = new_workspace("유지될 워크스페이스")
        await workspace_repo.create(workspace)
        await member_repo.create(new_member(workspace.id, user.id))
        await db_session.commit()

        await db_session.execute(
            text("DELETE FROM \"user\" WHERE id = :id"), {"id": user.id}
        )
        await db_session.commit()

        assert await member_repo.get_all_members(workspace.id) == []


# WorkspaceMemberRepository
class TestWorkspaceMemberRepository:
    @pytest.fixture
    async def workspace_with_owner(self, db_session):
        user = await UserRepository(db_session).create(new_user("owner@example.com"))

        workspace = new_workspace("멤버 테스트")
        await WorkspaceRepository(db_session).create(workspace)

        await WorkspaceMemberRepository(db_session).create(
            new_member(workspace.id, user.id, WorkspaceRole.OWNER)
        )
        await db_session.commit()

        return workspace, user

    async def test_get_member(self, db_session, workspace_with_owner):
        workspace, user = workspace_with_owner

        member = await WorkspaceMemberRepository(db_session).get_member(
            workspace.id, user.id
        )

        assert member is not None
        assert member.role == WorkspaceRole.OWNER

    async def test_get_member_not_found(self, db_session, workspace_with_owner):
        workspace, _ = workspace_with_owner

        assert (
            await WorkspaceMemberRepository(db_session).get_member(
                workspace.id, 999_999
            )
            is None
        )

    async def test_get_member_is_scoped_by_workspace(self, db_session, workspace_with_owner):
        """다른 워크스페이스 id 로는 조회되면 안 된다 (권한 경계의 핵심)."""
        _, user = workspace_with_owner

        other = new_workspace("다른 워크스페이스")
        await WorkspaceRepository(db_session).create(other)
        await db_session.commit()

        assert (
            await WorkspaceMemberRepository(db_session).get_member(other.id, user.id)
            is None
        )

    async def test_role_is_persisted_as_string(self, db_session, workspace_with_owner):
        """role 컬럼이 String(20) 이라 enum 값이 문자열로 저장되는지 확인."""
        workspace, user = workspace_with_owner

        result = await db_session.execute(
            text(
                "SELECT role FROM workspace_member "
                "WHERE workspace_id = :w AND user_id = :u"
            ),
            {"w": workspace.id, "u": user.id},
        )

        assert result.scalar_one() == "owner"

    async def test_get_all_members_ordered_by_joined_at(self, db_session):
        user_repo = UserRepository(db_session)
        users = [
            await user_repo.create(new_user(f"m{i}@example.com", name=f"유저{i}"))
            for i in range(3)
        ]

        workspace = new_workspace("정렬 테스트")
        await WorkspaceRepository(db_session).create(workspace)

        member_repo = WorkspaceMemberRepository(db_session)

        # 일부러 역순으로 넣고, joined_at 순으로 나오는지 본다
        for offset, user in zip([20, 10, 0], users):
            await member_repo.create(
                new_member(workspace.id, user.id, joined_offset_minutes=offset)
            )
        await db_session.commit()

        members = await member_repo.get_all_members(workspace.id)

        assert [m.user_id for m in members] == [
            users[2].id,
            users[1].id,
            users[0].id,
        ]

    async def test_get_all_members_empty(self, db_session):
        workspace = new_workspace("빈 워크스페이스")
        await WorkspaceRepository(db_session).create(workspace)
        await db_session.commit()

        assert (
            await WorkspaceMemberRepository(db_session).get_all_members(workspace.id)
            == []
        )

    async def test_duplicate_membership_violates_pk(self, db_session, workspace_with_owner):
        """(workspace_id, user_id) 복합 PK 라 같은 유저를 두 번 넣을 수 없다."""
        workspace, user = workspace_with_owner
        repo = WorkspaceMemberRepository(db_session)

        with pytest.raises(IntegrityError):
            await repo.create(new_member(workspace.id, user.id))

        await db_session.rollback()

    async def test_member_requires_existing_workspace(self, db_session):
        """존재하지 않는 워크스페이스에는 멤버를 넣을 수 없다 (FK)."""
        user = await UserRepository(db_session).create(new_user("fk@example.com"))
        repo = WorkspaceMemberRepository(db_session)

        with pytest.raises(IntegrityError):
            await repo.create(new_member(999_999, user.id))

        await db_session.rollback()

    async def test_delete(self, db_session, workspace_with_owner):
        workspace, user = workspace_with_owner
        repo = WorkspaceMemberRepository(db_session)

        member = await repo.get_member(workspace.id, user.id)
        await repo.delete(member)
        await db_session.commit()

        assert await repo.get_member(workspace.id, user.id) is None
