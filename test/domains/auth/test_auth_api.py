"""
app/domains/auth/router.py - auth API 통합 테스트.

여기서 검증하는 것 (서비스 로직은 이미 단위 테스트에서 다뤘다)
    - 라우팅 / 상태코드 / 응답 바디 형태
    - 요청 스키마 검증(422)
    - AppException -> HTTP 상태코드 + {"detail": ...} 매핑 (core/exception/handlers.py)

서비스 계층은 dependency_overrides 로 통째로 mock 한다.
실제 redis/DB 는 붙지 않는다 (ASGITransport 는 lifespan 도 실행하지 않는다).
"""

from unittest.mock import AsyncMock

import pytest

from app.domains.auth.dependencies import get_auth_service, get_oauth_service
from app.domains.auth.exceptions import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    InvalidOAuthStateError,
    InvalidVerificationCodeError,
    OAuthTicketInvalidError,
    SessionExpiredError,
    TokenInvalidError,
)
from app.domains.auth.schema import IssuedTokens
from app.domains.auth.service import AuthService
from app.domains.auth.oauth.service import OAuthService

pytestmark = pytest.mark.anyio


@pytest.fixture
def fake_auth_service(override_dependency) -> AsyncMock:
    """AuthService 를 통째로 대체한다. 라우터가 어떤 인자로 무엇을 호출하는지만 본다."""
    service = AsyncMock(spec=AuthService)
    override_dependency(get_auth_service, lambda: service)
    return service


@pytest.fixture
def fake_oauth_service(override_dependency) -> AsyncMock:
    service = AsyncMock(spec=OAuthService)
    override_dependency(get_oauth_service, lambda: service)
    return service


@pytest.fixture
def tokens() -> IssuedTokens:
    return IssuedTokens(access_token="access-token", refresh_token="refresh-token")


# ===========================================================================
# POST /auth/email/send
# ===========================================================================
class TestSendEmail:
    async def test_success(self, client, fake_auth_service):
        response = await client.post(
            "/auth/email/send",
            json={
                "email": "user@example.com",
                "password": "password123!",
                "name": "테스터",
            },
        )

        assert response.status_code == 200
        assert response.json() == {"message": "인증 이메일을 전송했습니다."}
        fake_auth_service.request_register_code.assert_awaited_once()

        # 라우터가 pydantic 모델을 그대로 서비스에 넘기는지 확인
        data = fake_auth_service.request_register_code.await_args.args[0]
        assert data.email == "user@example.com"
        assert data.name == "테스터"


    async def test_duplicate_email_returns_409(self, client, fake_auth_service):
        """AppException 이 status_code + detail 로 변환되는지 (핸들러 검증)."""
        fake_auth_service.request_register_code.side_effect = EmailAlreadyExistsError()

        response = await client.post(
            "/auth/email/send",
            json={
                "email": "user@example.com",
                "password": "password123!",
                "name": "테스터",
            },
        )

        assert response.status_code == 409
        assert response.json() == {"detail": "이미 사용 중인 이메일입니다."}

    @pytest.mark.parametrize(
        "payload, reason",
        [
            (
                {"email": "not-an-email", "password": "password123!", "name": "테스터"},
                "이메일 형식 아님",
            ),
            (
                {"email": "user@example.com", "password": "short", "name": "테스터"},
                "비밀번호 8자 미만",
            ),
            (
                {"email": "user@example.com", "password": "password123!", "name": ""},
                "이름 공백",
            ),
            (
                {"email": "user@example.com", "password": "password123!"},
                "이름 누락",
            ),
        ],
    )
    async def test_validation_errors(self, client, fake_auth_service, payload, reason):
        """스키마 검증 실패는 서비스까지 도달하지 않고 422 로 끊겨야 한다."""
        response = await client.post("/auth/email/send", json=payload)

        assert response.status_code == 422, reason
        fake_auth_service.request_register_code.assert_not_awaited()


# ===========================================================================
# POST /auth/email/resend
# ===========================================================================
class TestResendEmail:
    async def test_success(self, client, fake_auth_service):
        response = await client.post(
            "/auth/email/resend", json={"email": "user@example.com"}
        )

        assert response.status_code == 200
        assert response.json() == {"message": "인증 이메일을 재전송했습니다."}
        fake_auth_service.resend_register_code.assert_awaited_once()


# ===========================================================================
# POST /auth/register
# ===========================================================================
class TestRegister:
    async def test_success(self, client, fake_auth_service):
        response = await client.post(
            "/auth/register",
            json={"email": "user@example.com", "code": "abc123"},
        )

        assert response.status_code == 200
        assert response.json() == {"message": "회원가입이 완료되었습니다."}

    async def test_invalid_code_returns_400(self, client, fake_auth_service):
        fake_auth_service.register_user.side_effect = InvalidVerificationCodeError()

        response = await client.post(
            "/auth/register",
            json={"email": "user@example.com", "code": "wrong1"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "인증 코드가 올바르지 않습니다."

    @pytest.mark.parametrize("code", ["12345", "1234567"])
    async def test_code_must_be_six_chars(self, client, fake_auth_service, code):
        response = await client.post(
            "/auth/register", json={"email": "user@example.com", "code": code}
        )

        assert response.status_code == 422
        fake_auth_service.register_user.assert_not_awaited()


# ===========================================================================
# POST /auth/login
# ===========================================================================
class TestLogin:
    async def test_success(self, client, fake_auth_service, tokens):
        fake_auth_service.login_user.return_value = tokens

        response = await client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "password123!"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_type": "bearer",   # 클라이언트가 Authorization 헤더를 만들 때 쓴다
        }

    async def test_invalid_credentials_returns_401(self, client, fake_auth_service):
        fake_auth_service.login_user.side_effect = InvalidCredentialsError()

        response = await client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "wrong"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "이메일 또는 비밀번호가 올바르지 않습니다."

    async def test_missing_password(self, client, fake_auth_service):
        response = await client.post("/auth/login", json={"email": "user@example.com"})

        assert response.status_code == 422


# ===========================================================================
# POST /auth/logout
# ===========================================================================
class TestLogout:
    async def test_success(self, client, fake_auth_service):
        response = await client.post(
            "/auth/logout", json={"refresh_token": "refresh-token"}
        )

        assert response.status_code == 200
        assert response.json() == {"message": "로그아웃 성공!"}
        fake_auth_service.logout.assert_awaited_once_with("refresh-token")

    async def test_invalid_token_returns_401(self, client, fake_auth_service):
        fake_auth_service.logout.side_effect = TokenInvalidError()

        response = await client.post("/auth/logout", json={"refresh_token": "bad"})

        assert response.status_code == 401


# ===========================================================================
# POST /auth/reissue
# ===========================================================================
class TestReissue:
    async def test_success(self, client, fake_auth_service, tokens):
        fake_auth_service.reissue_token.return_value = tokens

        response = await client.post(
            "/auth/reissue", json={"refresh_token": "refresh-token"}
        )

        assert response.status_code == 200
        assert response.json()["access_token"] == "access-token"
        fake_auth_service.reissue_token.assert_awaited_once_with("refresh-token")

    async def test_revoked_session_returns_401(self, client, fake_auth_service):
        fake_auth_service.reissue_token.side_effect = SessionExpiredError()

        response = await client.post("/auth/reissue", json={"refresh_token": "old"})

        assert response.status_code == 401
        assert response.json()["detail"] == "Session expired or revoked"


# ===========================================================================
# GET /auth/oauth/{provider}/login
# ===========================================================================
class TestOAuthLogin:
    @pytest.mark.parametrize("provider", ["google", "github"])
    async def test_redirects_to_provider(self, client, fake_oauth_service, provider):
        """공급자 authorize URL 로 302/307 리다이렉트."""
        fake_oauth_service.create_login_url.return_value = (
            f"https://{provider}.test/authorize?state=abc"
        )

        response = await client.get(
            f"/auth/oauth/{provider}/login", follow_redirects=False
        )

        assert response.status_code == 307
        assert response.headers["location"] == f"https://{provider}.test/authorize?state=abc"

    async def test_unknown_provider_returns_422(self, client, fake_oauth_service):
        """경로 파라미터가 OAuthProvider enum 으로 검증된다."""
        response = await client.get("/auth/oauth/kakao/login", follow_redirects=False)

        assert response.status_code == 422
        fake_oauth_service.create_login_url.assert_not_awaited()


# ===========================================================================
# GET /auth/oauth/{provider}/callback
# ===========================================================================
class TestOAuthCallback:
    async def test_success(self, client, fake_oauth_service):
        """
        [현재 구현 확인용]
        운영에서는 프론트로 RedirectResponse 를 내보내야 하지만,
        지금 라우터는 디버깅용으로 {"wow": <url>} JSON 을 그대로 돌려준다.
        (router.py 의 주석 처리된 RedirectResponse 참고)
        리다이렉트로 되돌리면 이 테스트도 307 + location 검증으로 바꿔야 한다.
        """
        fake_oauth_service.handle_callback.return_value = (
            "http://localhost:3000/oauth/callback?ticket=t-1"
        )

        response = await client.get(
            "/auth/oauth/google/callback",
            params={"code": "auth-code", "state": "state-1"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "wow": "http://localhost:3000/oauth/callback?ticket=t-1"
        }

        fake_oauth_service.handle_callback.assert_awaited_once()
        kwargs = fake_oauth_service.handle_callback.await_args.kwargs
        assert kwargs["code"] == "auth-code"
        assert kwargs["state"] == "state-1"

    @pytest.mark.parametrize(
        "params",
        [
            {"state": "state-1"},   # code 누락
            {"code": "auth-code"},  # state 누락
        ],
    )
    async def test_missing_query_params(self, client, fake_oauth_service, params):
        response = await client.get("/auth/oauth/google/callback", params=params)

        assert response.status_code == 422

    async def test_invalid_state_returns_401(self, client, fake_oauth_service):
        fake_oauth_service.handle_callback.side_effect = InvalidOAuthStateError()

        response = await client.get(
            "/auth/oauth/google/callback",
            params={"code": "c", "state": "forged"},
        )

        assert response.status_code == 401


# ===========================================================================
# POST /auth/oauth/exchange
# ===========================================================================
class TestOAuthExchange:
    async def test_success(self, client, fake_auth_service, tokens):
        """프론트가 받은 1회용 티켓을 실제 토큰 쌍으로 교환한다."""
        fake_auth_service.exchange_oauth_ticket.return_value = tokens

        response = await client.post("/auth/oauth/exchange", json={"ticket": "t-1"})

        assert response.status_code == 200
        assert response.json()["refresh_token"] == "refresh-token"
        fake_auth_service.exchange_oauth_ticket.assert_awaited_once_with("t-1")

    async def test_invalid_ticket_returns_401(self, client, fake_auth_service):
        fake_auth_service.exchange_oauth_ticket.side_effect = OAuthTicketInvalidError()

        response = await client.post("/auth/oauth/exchange", json={"ticket": "used"})

        assert response.status_code == 401


# ===========================================================================
# 예외 핸들러 (core/exception/handlers.py)
# ===========================================================================
class TestExceptionHandlers:
    async def test_unexpected_error_returns_500_without_leaking_details(
        self, client_no_raise, fake_auth_service
    ):
        """
        AppException 이 아닌 예외는 500 + 고정 메시지로 감춰야 한다.
        (내부 에러 메시지가 클라이언트로 새면 안 된다)

        Starlette 의 ServerErrorMiddleware 는 응답을 만든 뒤 예외를 재-raise 하므로
        raise_app_exceptions=False 인 client_no_raise 를 사용한다.
        """
        fake_auth_service.login_user.side_effect = RuntimeError(
            "db password = supersecret"
        )

        response = await client_no_raise.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "password123!"},
        )

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal Server Error"}
        assert "supersecret" not in response.text