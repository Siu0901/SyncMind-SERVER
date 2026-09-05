"""
app/domains/user/router.py - user API 통합 테스트.

/users/me 는 유일하게 인증이 필요한 엔드포인트라서,
여기서는 get_current_user 를 mock 하지 않고 '실제로' 통과시킨다.
대신 그 아래 인프라(redis / session / repository)만 갈아끼워서
HTTP 요청 -> 토큰 파싱 -> 세션 검증 -> 유저 조회 전체 경로를 한 번에 확인한다.

ASGITransport 는 lifespan 을 실행하지 않으므로 get_redis() 오버라이드는 필수다.
(오버라이드하지 않으면 redis 미초기화 예외 때문에 500 이 난다)
"""

import pytest

from app.core.database import get_session
from app.core.dependencies import get_auth_manager
from app.core.redis import get_redis
from app.domains.user.dependencies import get_user_repository

pytestmark = pytest.mark.anyio


@pytest.fixture
def wire_auth_infra(
    override_dependency, mock_redis, mock_session, mock_user_repo, auth_manager
):
    """get_current_user 가 의존하는 하위 인프라를 전부 mock 으로 교체한다."""
    override_dependency(get_redis, lambda: mock_redis)
    override_dependency(get_session, lambda: mock_session)
    override_dependency(get_user_repository, lambda: mock_user_repo)
    override_dependency(get_auth_manager, lambda: auth_manager)


class TestGetMe:
    async def test_success(
        self, client, wire_auth_infra, auth_manager, mock_redis, mock_user_repo,
        redis_session_value, make_user,
    ):
        """유효한 access 토큰 -> 내 정보 반환."""
        access_token, access_jti = auth_manager.create_access_token(7, "sid-me")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=7, access_jti=access_jti, refresh_jti="r"
        )
        mock_user_repo.get_by_id.return_value = make_user(
            user_id=7, email="me@example.com", name="나"
        )

        response = await client.get(
            "/users/me", headers={"Authorization": f"Bearer {access_token}"}
        )

        assert response.status_code == 200

        body = response.json()
        assert body["id"] == 7
        assert body["email"] == "me@example.com"
        assert body["name"] == "나"
        assert body["is_active"] is True

    async def test_password_hash_is_never_exposed(
        self, client, wire_auth_infra, auth_manager, mock_redis, mock_user_repo,
        redis_session_value, make_user,
    ):
        """
        UserResponse 에 password_hash 필드가 없어야 한다.
        응답 스키마에 필드를 추가하다가 실수로 노출되는 것을 막는 회귀 테스트.
        """
        access_token, access_jti = auth_manager.create_access_token(7, "sid-me")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=7, access_jti=access_jti, refresh_jti="r"
        )
        user = make_user(user_id=7)
        mock_user_repo.get_by_id.return_value = user

        response = await client.get(
            "/users/me", headers={"Authorization": f"Bearer {access_token}"}
        )

        body = response.json()
        assert "password_hash" not in body
        assert user.password_hash not in response.text

    async def test_without_token_returns_401(self, client, wire_auth_infra):
        response = await client.get("/users/me")

        assert response.status_code == 401
        assert response.json() == {"detail": "Token invalid"}

    async def test_with_malformed_token_returns_401(self, client, wire_auth_infra):
        response = await client.get(
            "/users/me", headers={"Authorization": "Bearer not-a-jwt"}
        )

        assert response.status_code == 401

    async def test_with_refresh_token_returns_401(
        self, client, wire_auth_infra, auth_manager
    ):
        """refresh 토큰으로 보호된 API 를 호출하면 거부돼야 한다."""
        refresh_token, _ = auth_manager.create_refresh_token(7, "sid-me")

        response = await client.get(
            "/users/me", headers={"Authorization": f"Bearer {refresh_token}"}
        )

        assert response.status_code == 401

    async def test_revoked_session_returns_401(
        self, client, wire_auth_infra, auth_manager, mock_redis
    ):
        """로그아웃/탈퇴로 세션이 삭제된 뒤에는 남아있는 토큰도 무효."""
        access_token, _ = auth_manager.create_access_token(7, "sid-me")
        mock_redis.hgetall.return_value = {}

        response = await client.get(
            "/users/me", headers={"Authorization": f"Bearer {access_token}"}
        )

        assert response.status_code == 401
        assert response.json() == {"detail": "Session expired or revoked"}

    async def test_inactive_user_returns_403(
        self, client, wire_auth_infra, auth_manager, mock_redis, mock_user_repo,
        redis_session_value, make_user,
    ):
        access_token, access_jti = auth_manager.create_access_token(7, "sid-me")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=7, access_jti=access_jti, refresh_jti="r"
        )
        mock_user_repo.get_by_id.return_value = make_user(user_id=7, is_active=False)

        response = await client.get(
            "/users/me", headers={"Authorization": f"Bearer {access_token}"}
        )

        assert response.status_code == 403
        assert response.json() == {"detail": "비활성화된 계정입니다"}
