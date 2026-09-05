"""
app/domains/auth/dependencies.py - 인증 의존성 단위 테스트.

get_current_user 는 모든 보호된 엔드포인트가 통과하는 실질적인 인증 게이트다.
FastAPI 를 거치지 않고 함수를 직접 호출해서 분기를 하나씩 확인한다.

핵심 규칙
    - access 토큰만 허용 (refresh 토큰으로 API 호출 불가)
    - JWT 가 유효해도 redis 세션이 살아있어야 한다 (= 서버측 강제 로그아웃 가능)
    - 세션의 access_jti 와 토큰의 jti 가 일치해야 한다 (= 재발급 시 옛 토큰 무효화)
"""

from types import SimpleNamespace

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.domains.auth.dependencies import (
    get_auth_service,
    get_current_user,
    get_oauth_service,
)
from app.domains.auth.enums import OAuthProvider
from app.domains.auth.exceptions import (
    InactiveUserError,
    OAuthProviderError,
    SessionExpiredError,
    TokenInvalidError,
)
from app.domains.auth.oauth.factory import OAuthClientFactory
from app.domains.auth.oauth.github import GitHubOAuthClient
from app.domains.auth.oauth.google import GoogleOAuthClient
from app.domains.auth.oauth.service import OAuthService
from app.domains.auth.service import AuthService

pytestmark = pytest.mark.anyio


def bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def call_get_current_user(credentials, auth_manager, user_repository, redis):
    """인자 순서를 매번 쓰기 번거로워서 감싼 헬퍼."""
    return await get_current_user(
        credentials=credentials,
        auth_manager=auth_manager,
        user_repository=user_repository,
        redis=redis,
    )


# ===========================================================================
# get_current_user
# ===========================================================================
class TestGetCurrentUser:
    async def test_no_authorization_header(
        self, auth_manager, mock_user_repo, mock_redis
    ):
        """
        Authorization 헤더가 없으면 credentials 가 None 이다 (HTTPBearer(auto_error=False)).
        -> 401
        """
        with pytest.raises(TokenInvalidError):
            await call_get_current_user(None, auth_manager, mock_user_repo, mock_redis)

    async def test_refresh_token_rejected(
        self, auth_manager, mock_user_repo, mock_redis
    ):
        """refresh 토큰으로는 API 를 호출할 수 없어야 한다."""
        refresh_token, _ = auth_manager.create_refresh_token(1, "sid")

        with pytest.raises(TokenInvalidError):
            await call_get_current_user(
                bearer(refresh_token), auth_manager, mock_user_repo, mock_redis
            )

    async def test_invalid_token(self, auth_manager, mock_user_repo, mock_redis):
        with pytest.raises(TokenInvalidError):
            await call_get_current_user(
                bearer("garbage"), auth_manager, mock_user_repo, mock_redis
            )

    async def test_session_revoked(self, auth_manager, mock_user_repo, mock_redis):
        """
        JWT 자체는 유효하지만 서버에서 세션을 날린 경우(로그아웃/탈퇴) -> 401.
        JWT 무상태 인증의 취소 불가 문제를 redis 세션으로 보완하는 부분이다.
        """
        access_token, _ = auth_manager.create_access_token(1, "sid")
        mock_redis.hgetall.return_value = {}

        with pytest.raises(SessionExpiredError):
            await call_get_current_user(
                bearer(access_token), auth_manager, mock_user_repo, mock_redis
            )

    async def test_stale_access_token_after_reissue(
        self, auth_manager, mock_user_repo, mock_redis, redis_session_value
    ):
        """재발급으로 access_jti 가 회전된 뒤의 옛 access 토큰 -> 401."""
        access_token, _ = auth_manager.create_access_token(1, "sid")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=1, access_jti="새로운-jti", refresh_jti="r"
        )

        with pytest.raises(SessionExpiredError):
            await call_get_current_user(
                bearer(access_token), auth_manager, mock_user_repo, mock_redis
            )

    async def test_user_id_mismatch(
        self, auth_manager, mock_user_repo, mock_redis, redis_session_value
    ):
        """토큰의 sub 와 세션의 user_id 불일치 -> 401 (세션 하이재킹 방어)."""
        access_token, access_jti = auth_manager.create_access_token(1, "sid")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=999, access_jti=access_jti, refresh_jti="r"
        )

        with pytest.raises(TokenInvalidError):
            await call_get_current_user(
                bearer(access_token), auth_manager, mock_user_repo, mock_redis
            )

    async def test_session_without_user_id(
        self, auth_manager, mock_user_repo, mock_redis
    ):
        """세션 해시가 깨져 user_id 가 없는 경우 -> 401."""
        access_token, access_jti = auth_manager.create_access_token(1, "sid")
        mock_redis.hgetall.return_value = {"access_jti": access_jti}

        with pytest.raises(SessionExpiredError):
            await call_get_current_user(
                bearer(access_token), auth_manager, mock_user_repo, mock_redis
            )

    async def test_deleted_user(
        self, auth_manager, mock_user_repo, mock_redis, redis_session_value
    ):
        access_token, access_jti = auth_manager.create_access_token(1, "sid")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=1, access_jti=access_jti, refresh_jti="r"
        )
        mock_user_repo.get_by_id.return_value = None

        with pytest.raises(TokenInvalidError):
            await call_get_current_user(
                bearer(access_token), auth_manager, mock_user_repo, mock_redis
            )

    async def test_inactive_user(
        self, auth_manager, mock_user_repo, mock_redis, redis_session_value, make_user
    ):
        """탈퇴 유저는 토큰이 살아있어도 접근 불가 -> 403."""
        access_token, access_jti = auth_manager.create_access_token(1, "sid")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=1, access_jti=access_jti, refresh_jti="r"
        )
        mock_user_repo.get_by_id.return_value = make_user(user_id=1, is_active=False)

        with pytest.raises(InactiveUserError):
            await call_get_current_user(
                bearer(access_token), auth_manager, mock_user_repo, mock_redis
            )

    @pytest.mark.parametrize("as_bytes", [False, True])
    async def test_success(
        self, auth_manager, mock_user_repo, mock_redis, redis_session_value,
        make_user, as_bytes,
    ):
        """정상 인증 -> User 반환. (redis 응답이 str/bytes 어느 쪽이든 동작)"""
        access_token, access_jti = auth_manager.create_access_token(7, "sid")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=7, access_jti=access_jti, refresh_jti="r", as_bytes=as_bytes
        )
        expected = make_user(user_id=7)
        mock_user_repo.get_by_id.return_value = expected

        user = await call_get_current_user(
            bearer(access_token), auth_manager, mock_user_repo, mock_redis
        )

        assert user is expected
        mock_redis.hgetall.assert_awaited_once_with("auth:session:sid")
        mock_user_repo.get_by_id.assert_awaited_once_with(7)


# ===========================================================================
# 서비스 팩토리 의존성 (배선 확인)
# ===========================================================================
class TestServiceFactories:
    async def test_get_auth_service_wiring(
        self, mock_session, auth_manager, mock_user_repo, mock_redis
    ):
        """주입받은 객체가 그대로 서비스에 꽂히는지 확인 (배선 실수 방지)."""
        service = await get_auth_service(
            session=mock_session,
            auth_manager=auth_manager,
            user_repository=mock_user_repo,
            redis=mock_redis,
        )

        assert isinstance(service, AuthService)
        assert service.session is mock_session
        assert service.redis is mock_redis
        assert service.auth_manager is auth_manager
        assert service.users_repo is mock_user_repo

    async def test_get_oauth_service_wiring(
        self, mock_session, auth_manager, mock_user_repo, mock_redis,
        mock_oauth_repo, mock_oauth_factory,
    ):
        service = await get_oauth_service(
            session=mock_session,
            redis=mock_redis,
            auth_manager=auth_manager,
            user_repository=mock_user_repo,
            auth_account_repo=mock_oauth_repo,
            oauth_factory=mock_oauth_factory,
        )

        assert isinstance(service, OAuthService)
        assert service.oauth_accounts is mock_oauth_repo
        assert service.oauth_factory is mock_oauth_factory


# ===========================================================================
# OAuthClientFactory
# ===========================================================================
class TestOAuthClientFactory:
    def test_returns_matching_client(self):
        google = GoogleOAuthClient()
        github = GitHubOAuthClient()
        factory = OAuthClientFactory(google=google, github=github)

        assert factory.get(OAuthProvider.GOOGLE) is google
        assert factory.get(OAuthProvider.GITHUB) is github

    def test_unknown_provider(self):
        """
        방어 로직 확인.
        라우터가 OAuthProvider enum 으로 검증하기 때문에 실제로는 도달하지 않지만,
        공급자를 추가하면서 factory 갱신을 빠뜨리면 여기서 걸린다.
        """
        factory = OAuthClientFactory(
            google=GoogleOAuthClient(), github=GitHubOAuthClient()
        )

        with pytest.raises(OAuthProviderError):
            factory.get(SimpleNamespace(value="kakao"))
