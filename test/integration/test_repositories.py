"""
UserRepository / OAuthAccountRepository 통합 테스트 (실제 PostgreSQL).

실행 방법은 test/integration/conftest.py 상단 참고.
TEST_DATABASE_URL 이 없으면 전부 skip 된다.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.domains.auth.enums import OAuthProvider
from app.domains.auth.model import OAuthAccount
from app.domains.auth.repository import OAuthAccountRepository
from app.domains.user.model import User
from app.domains.user.repository import UserRepository

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


def new_user(email: str = "repo@example.com", **kwargs) -> User:
    return User(
        email=email,
        password_hash=kwargs.pop("password_hash", "hash"),
        name=kwargs.pop("name", "테스터"),
        email_verified=kwargs.pop("email_verified", True),
        is_active=kwargs.pop("is_active", True),
        **kwargs,
    )


# ===========================================================================
# UserRepository
# ===========================================================================
class TestUserRepository:
    async def test_create_assigns_id_and_server_defaults(self, db_session):
        """
        DB 가 채워주는 값들(PK, created_at, updated_at)이 실제로 들어오는지 확인.
        모델의 sa_column server_default 설정이 깨지면 여기서 잡힌다.
        """
        repo = UserRepository(db_session)

        user = await repo.create(new_user())

        assert user.id is not None
        assert user.created_at is not None
        assert user.updated_at is not None

    async def test_get_by_id(self, db_session):
        repo = UserRepository(db_session)
        created = await repo.create(new_user())

        found = await repo.get_by_id(created.id)

        assert found is not None
        assert found.email == "repo@example.com"

    async def test_get_by_id_not_found(self, db_session):
        repo = UserRepository(db_session)

        assert await repo.get_by_id(999_999) is None

    async def test_get_by_email(self, db_session):
        repo = UserRepository(db_session)
        await repo.create(new_user(email="find@example.com"))

        found = await repo.get_by_email("find@example.com")

        assert found is not None
        assert found.name == "테스터"

    async def test_get_by_email_is_case_sensitive(self, db_session):
        """
        DB 조회는 대소문자를 구분한다.
        그래서 서비스 계층이 normalize_email 로 소문자화한 뒤 조회하는 것이 필수다.
        (이 규칙이 깨지면 같은 이메일로 중복 가입이 가능해진다)
        """
        repo = UserRepository(db_session)
        await repo.create(new_user(email="case@example.com"))

        assert await repo.get_by_email("CASE@EXAMPLE.COM") is None

    async def test_duplicate_email_violates_unique_constraint(self, db_session):
        """email unique 인덱스가 실제로 걸려 있는지 확인 (경쟁 상황의 마지막 방어선)."""
        repo = UserRepository(db_session)
        await repo.create(new_user(email="dup@example.com"))

        with pytest.raises(IntegrityError):
            await repo.create(new_user(email="dup@example.com"))

        await db_session.rollback()

    async def test_create_commits_internally(self, db_session, db_engine):
        """
        [현재 구현 확인용]
        UserRepository.create() 는 내부에서 commit 을 한다.
        즉 호출한 서비스가 나중에 rollback 해도 이 유저는 되돌릴 수 없다.

        OAuthService._resolve_user 는 User 생성 -> OAuthAccount 생성 -> commit 순서라서,
        중간에 실패하면 소셜 계정 매핑이 없는 '고아 User' 가 남는다.
        create() 에서 commit 을 걷어내고 서비스가 트랜잭션을 잡도록 바꾸면
        이 테스트는 반대로(=rollback 하면 사라짐) 뒤집어야 한다.
        """
        repo = UserRepository(db_session)
        created = await repo.create(new_user(email="commit@example.com"))

        # rollback 은 세션의 객체들을 expire 시킨다.
        # 이후 created.id 에 접근하면 lazy load(동기 IO)가 터지므로 미리 꺼내둔다.
        user_id = created.id

        await db_session.rollback()

        # 다른 세션에서 조회해도 남아 있다 = 이미 커밋됐다는 뜻
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from sqlmodel.ext.asyncio.session import AsyncSession

        factory = async_sessionmaker(bind=db_engine, class_=AsyncSession)

        async with factory() as other_session:
            other = await UserRepository(other_session).get_by_id(user_id)

        assert other is not None


# ===========================================================================
# OAuthAccountRepository
# ===========================================================================
class TestOAuthAccountRepository:
    async def test_create_and_lookup_by_provider_identity(self, db_session):
        """소셜 재로그인 경로에서 쓰는 조회 (provider + provider_user_id)."""
        user = await UserRepository(db_session).create(new_user(email="o1@example.com"))
        repo = OAuthAccountRepository(db_session)

        await repo.create(
            OAuthAccount(
                user_id=user.id,
                provider=OAuthProvider.GOOGLE,
                provider_user_id="google-uid",
                provider_email=user.email,
            )
        )
        await db_session.commit()

        found = await repo.get_by_provider_identity(
            OAuthProvider.GOOGLE, "google-uid"
        )

        assert found is not None
        assert found.user_id == user.id

    async def test_lookup_is_scoped_by_provider(self, db_session):
        """provider 가 다르면 같은 provider_user_id 라도 잡히면 안 된다."""
        user = await UserRepository(db_session).create(new_user(email="o2@example.com"))
        repo = OAuthAccountRepository(db_session)

        await repo.create(
            OAuthAccount(
                user_id=user.id,
                provider=OAuthProvider.GOOGLE,
                provider_user_id="same-uid",
            )
        )
        await db_session.commit()

        assert await repo.get_by_provider_identity(
            OAuthProvider.GITHUB, "same-uid"
        ) is None

    async def test_get_by_user_provider(self, db_session):
        user = await UserRepository(db_session).create(new_user(email="o3@example.com"))
        repo = OAuthAccountRepository(db_session)

        await repo.create(
            OAuthAccount(
                user_id=user.id,
                provider=OAuthProvider.GITHUB,
                provider_user_id="gh-uid",
            )
        )
        await db_session.commit()

        found = await repo.get_by_user_provider(user.id, OAuthProvider.GITHUB)

        assert found is not None
        assert found.provider_user_id == "gh-uid"

    async def test_not_found_returns_none(self, db_session):
        repo = OAuthAccountRepository(db_session)

        assert await repo.get_by_provider_identity(
            OAuthProvider.GOOGLE, "nope"
        ) is None
        assert await repo.get_by_user_provider(999_999, OAuthProvider.GOOGLE) is None

    async def test_same_provider_account_cannot_be_linked_twice(self, db_session):
        """
        uq_oauth_provider_user 제약 확인.
        하나의 소셜 계정이 서로 다른 유저에 연결되면 계정 탈취가 가능해진다.
        """
        user_a = await UserRepository(db_session).create(new_user(email="a@example.com"))
        user_b = await UserRepository(db_session).create(new_user(email="b@example.com"))
        repo = OAuthAccountRepository(db_session)

        await repo.create(
            OAuthAccount(
                user_id=user_a.id,
                provider=OAuthProvider.GOOGLE,
                provider_user_id="shared-uid",
            )
        )
        await db_session.commit()

        with pytest.raises(IntegrityError):
            await repo.create(
                OAuthAccount(
                    user_id=user_b.id,
                    provider=OAuthProvider.GOOGLE,
                    provider_user_id="shared-uid",
                )
            )

        await db_session.rollback()

    async def test_user_cannot_link_same_provider_twice(self, db_session):
        """uq_user_oauth_provider 제약 확인 (한 유저당 공급자별 1개)."""
        user = await UserRepository(db_session).create(new_user(email="c@example.com"))
        repo = OAuthAccountRepository(db_session)

        await repo.create(
            OAuthAccount(
                user_id=user.id,
                provider=OAuthProvider.GOOGLE,
                provider_user_id="uid-1",
            )
        )
        await db_session.commit()

        with pytest.raises(IntegrityError):
            await repo.create(
                OAuthAccount(
                    user_id=user.id,
                    provider=OAuthProvider.GOOGLE,
                    provider_user_id="uid-2",
                )
            )

        await db_session.rollback()

    async def test_create_does_not_commit(self, db_session, db_engine):
        """
        OAuthAccountRepository.create() 는 flush 만 한다 (UserRepository 와 반대).
        따라서 서비스가 commit 을 호출해야 실제로 저장된다.
        두 리포지토리의 트랜잭션 정책이 서로 다르다는 점을 박제해두는 테스트.
        """
        user = await UserRepository(db_session).create(new_user(email="d@example.com"))
        repo = OAuthAccountRepository(db_session)

        account = await repo.create(
            OAuthAccount(
                user_id=user.id,
                provider=OAuthProvider.GOOGLE,
                provider_user_id="uncommitted",
            )
        )

        assert account.id is not None       # flush 로 PK 는 채워진다
        await db_session.rollback()         # 커밋 전이므로 되돌릴 수 있다

        from sqlalchemy.ext.asyncio import async_sessionmaker
        from sqlmodel.ext.asyncio.session import AsyncSession

        factory = async_sessionmaker(bind=db_engine, class_=AsyncSession)

        async with factory() as other_session:
            found = await OAuthAccountRepository(
                other_session
            ).get_by_provider_identity(OAuthProvider.GOOGLE, "uncommitted")

        assert found is None
