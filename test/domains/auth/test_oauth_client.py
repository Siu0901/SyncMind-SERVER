"""
app/domains/auth/oauth/google.py, github.py - OAuth 클라이언트 단위 테스트.

이 계층은 '외부 HTTP 응답을 우리 도메인 스키마로 옮기고, 실패를 우리 예외로 번역'하는 게 전부다.
따라서 검증 포인트도 두 가지다.
    1. 정상 응답 -> OAuthUserInfo 매핑이 맞는가
    2. 각종 실패(HTTP 에러 / 토큰 없음 / 이메일 없음) -> 올바른 도메인 예외로 번역되는가

httpx 목킹 방식
    respx 가 설치돼 있지 않고, 클라이언트가 메서드 내부에서
    `async with httpx.AsyncClient(...)` 를 직접 만들기 때문에 주입도 불가능하다.
    그래서 httpx.AsyncClient 자체를 가짜 클래스로 monkeypatch 한다.
    응답 객체는 진짜 httpx.Response 를 쓰므로 raise_for_status() / .json() 은 실제 동작 그대로다.
    (respx 를 추가하면 fake_http 픽스처만 걷어내면 된다)
"""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.domains.auth import oauth as oauth_pkg  # noqa: F401  (패키지 로드 보장)
from app.domains.auth.enums import OAuthProvider
from app.domains.auth.exceptions import (
    OAuthEmailNotFoundError,
    OAuthProviderError,
)
from app.domains.auth.oauth import github as github_module
from app.domains.auth.oauth import google as google_module
from app.domains.auth.oauth.github import GitHubOAuthClient
from app.domains.auth.oauth.google import GoogleOAuthClient

pytestmark = pytest.mark.anyio


# ===========================================================================
# httpx 목킹 도구
# ===========================================================================
def json_response(payload: dict | list, *, status: int = 200, url: str = "https://x.test"):
    """진짜 httpx.Response 를 만든다. request 를 붙여야 raise_for_status() 가 동작한다."""
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("GET", url),
    )


@pytest.fixture
def fake_http(monkeypatch):
    """
    httpx.AsyncClient 를 URL -> 응답 매핑 테이블로 대체한다.

        calls = fake_http(google_module, {TOKEN_URL: resp, USERINFO_URL: resp})

    매핑 값으로 Exception 인스턴스를 주면 그 예외를 던진다 (네트워크 장애 재현).
    반환값 calls 에는 (method, url, kwargs) 가 순서대로 쌓인다.
    """

    def _install(module, routes: dict):
        calls: list[tuple[str, str, dict]] = []

        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                self.init_kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            def _resolve(self, method: str, url: str, kwargs: dict):
                calls.append((method, url, kwargs))

                if url not in routes:
                    raise AssertionError(f"목킹되지 않은 요청: {method} {url}")

                result = routes[url]

                if isinstance(result, Exception):
                    raise result

                return result

            async def post(self, url, **kwargs):
                return self._resolve("POST", url, kwargs)

            async def get(self, url, **kwargs):
                return self._resolve("GET", url, kwargs)

        monkeypatch.setattr(module.httpx, "AsyncClient", _FakeAsyncClient)

        return calls

    return _install


# ===========================================================================
# Google
# ===========================================================================
class TestGoogleAuthorizationUrl:
    def test_url_params(self, settings):
        """authorize URL 에 필수 파라미터가 정확히 실려야 한다."""
        url = GoogleOAuthClient().create_authorization_url("state-123")

        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        assert url.startswith(settings.GOOGLE_AUTH_URL)
        assert params["client_id"] == [settings.GOOGLE_CLIENT_ID]
        assert params["redirect_uri"] == [settings.GOOGLE_REDIRECT_URI]
        assert params["response_type"] == ["code"]
        assert params["state"] == ["state-123"]
        assert "email" in params["scope"][0]

    def test_client_secret_is_not_exposed(self, settings):
        """authorize URL 은 브라우저로 나가므로 secret 이 절대 포함되면 안 된다."""
        url = GoogleOAuthClient().create_authorization_url("state-123")

        assert settings.GOOGLE_CLIENT_SECRET not in url


class TestGoogleGetUserInfo:
    async def test_success(self, fake_http, settings):
        """정상 응답 -> OAuthUserInfo 매핑."""
        calls = fake_http(
            google_module,
            {
                settings.GOOGLE_TOKEN_URL: json_response({"access_token": "at-1"}),
                settings.GOOGLE_USERINFO_URL: json_response(
                    {
                        "sub": 1234567890,          # 구글은 숫자로 줄 수도 있다 -> str 변환 확인
                        "email": "user@example.com",
                        "name": "구글유저",
                        "picture": "https://cdn.google.test/p.png",
                    }
                ),
            },
        )

        info = await GoogleOAuthClient().get_user_info("auth-code")

        assert info.provider == OAuthProvider.GOOGLE
        assert info.provider_user_id == "1234567890"
        assert info.email == "user@example.com"
        assert info.name == "구글유저"
        assert info.profile_image_url == "https://cdn.google.test/p.png"

        # --- 토큰 교환 요청 본문 검증 ---
        token_call = calls[0]
        assert token_call[0] == "POST"
        assert token_call[1] == settings.GOOGLE_TOKEN_URL
        assert token_call[2]["data"]["code"] == "auth-code"
        assert token_call[2]["data"]["grant_type"] == "authorization_code"

        # --- userinfo 요청은 발급받은 access_token 을 Bearer 로 사용 ---
        userinfo_call = calls[1]
        assert userinfo_call[2]["headers"]["Authorization"] == "Bearer at-1"

    async def test_name_falls_back_to_email(self, fake_http, settings):
        """name 이 없으면 이메일을 이름으로 쓴다 (User.name 은 nullable 이 아님)."""
        fake_http(
            google_module,
            {
                settings.GOOGLE_TOKEN_URL: json_response({"access_token": "at-1"}),
                settings.GOOGLE_USERINFO_URL: json_response(
                    {"sub": "1", "email": "user@example.com"}
                ),
            },
        )

        info = await GoogleOAuthClient().get_user_info("code")

        assert info.name == "user@example.com"
        assert info.profile_image_url is None

    async def test_token_endpoint_http_error(self, fake_http, settings):
        """토큰 엔드포인트가 4xx -> 502(OAuthProviderError) 로 번역."""
        fake_http(
            google_module,
            {
                settings.GOOGLE_TOKEN_URL: json_response(
                    {"error": "invalid_grant"}, status=400
                ),
            },
        )

        with pytest.raises(OAuthProviderError):
            await GoogleOAuthClient().get_user_info("bad-code")

    async def test_token_response_without_access_token(self, fake_http, settings):
        """200 이지만 access_token 이 없는 비정상 응답 -> OAuthProviderError."""
        fake_http(
            google_module,
            {
                settings.GOOGLE_TOKEN_URL: json_response({"scope": "email"}),
            },
        )

        with pytest.raises(OAuthProviderError):
            await GoogleOAuthClient().get_user_info("code")

    async def test_network_error(self, fake_http, settings):
        """네트워크 장애도 도메인 예외로 감싸서 내보낸다 (httpx 예외 누출 금지)."""
        fake_http(
            google_module,
            {
                settings.GOOGLE_TOKEN_URL: httpx.ConnectError("connection refused"),
            },
        )

        with pytest.raises(OAuthProviderError):
            await GoogleOAuthClient().get_user_info("code")

    async def test_userinfo_without_email(self, fake_http, settings):
        """이메일 없이는 계정을 만들 수 없다 -> 400(OAuthEmailNotFoundError)."""
        fake_http(
            google_module,
            {
                settings.GOOGLE_TOKEN_URL: json_response({"access_token": "at-1"}),
                settings.GOOGLE_USERINFO_URL: json_response({"sub": "1"}),
            },
        )

        with pytest.raises(OAuthEmailNotFoundError):
            await GoogleOAuthClient().get_user_info("code")


# ===========================================================================
# GitHub
# ===========================================================================
class TestGitHubAuthorizationUrl:
    def test_url_params(self, settings):
        url = GitHubOAuthClient().create_authorization_url("state-abc")

        params = parse_qs(urlparse(url).query)

        assert url.startswith(settings.GITHUB_AUTH_URL)
        assert params["client_id"] == [settings.GITHUB_CLIENT_ID]
        assert params["redirect_uri"] == [settings.GITHUB_REDIRECT_URI]
        assert params["state"] == ["state-abc"]
        # 이메일을 따로 조회해야 하므로 user:email 스코프가 반드시 필요하다
        assert "user:email" in params["scope"][0]

    def test_client_secret_is_not_exposed(self, settings):
        url = GitHubOAuthClient().create_authorization_url("state-abc")

        assert settings.GITHUB_CLIENT_SECRET not in url


class TestGitHubPrimaryEmail:
    """
    _get_primary_email 은 순수 함수라 HTTP 없이 바로 검증한다.
    깃허브는 이메일을 배열로 주고 primary 플래그가 붙는다.
    """

    def test_prefers_primary(self):
        emails = [
            {"email": "second@example.com", "primary": False},
            {"email": "primary@example.com", "primary": True},
        ]

        assert GitHubOAuthClient._get_primary_email(emails) == "primary@example.com"

    def test_falls_back_to_first_email(self):
        """primary 플래그가 하나도 없으면 첫 번째 이메일을 쓴다."""
        emails = [
            {"email": "first@example.com", "primary": False},
            {"email": "second@example.com", "primary": False},
        ]

        assert GitHubOAuthClient._get_primary_email(emails) == "first@example.com"

    def test_empty_list(self):
        assert GitHubOAuthClient._get_primary_email([]) is None

    def test_entries_without_email_are_skipped(self):
        emails = [{"primary": False}, {"email": "ok@example.com", "primary": False}]

        assert GitHubOAuthClient._get_primary_email(emails) == "ok@example.com"


class TestGitHubGetUserInfo:
    async def test_success(self, fake_http, settings):
        calls = fake_http(
            github_module,
            {
                settings.GITHUB_TOKEN_URL: json_response({"access_token": "gh-at"}),
                settings.GITHUB_USER_URL: json_response(
                    {
                        "id": 42,
                        "login": "octocat",
                        "name": "깃허브유저",
                        "avatar_url": "https://avatars.github.test/u/42",
                    }
                ),
                settings.GITHUB_EMAILS_URL: json_response(
                    [
                        {"email": "public@example.com", "primary": False},
                        {"email": "primary@example.com", "primary": True},
                    ]
                ),
            },
        )

        info = await GitHubOAuthClient().get_user_info("code")

        assert info.provider == OAuthProvider.GITHUB
        assert info.provider_user_id == "42"          # int -> str 변환
        assert info.email == "primary@example.com"    # primary 우선
        assert info.name == "깃허브유저"
        assert info.profile_image_url == "https://avatars.github.test/u/42"

        # 토큰 교환 시 JSON 응답을 받기 위한 Accept 헤더가 필요하다 (깃허브 기본은 폼 인코딩)
        assert calls[0][2]["headers"]["Accept"] == "application/json"

    async def test_name_falls_back_to_login(self, fake_http, settings):
        """name 미설정 계정이 흔하다 -> login 으로 대체."""
        fake_http(
            github_module,
            {
                settings.GITHUB_TOKEN_URL: json_response({"access_token": "gh-at"}),
                settings.GITHUB_USER_URL: json_response({"id": 42, "login": "octocat"}),
                settings.GITHUB_EMAILS_URL: json_response(
                    [{"email": "primary@example.com", "primary": True}]
                ),
            },
        )

        info = await GitHubOAuthClient().get_user_info("code")

        assert info.name == "octocat"

    async def test_no_email_available(self, fake_http, settings):
        """이메일을 비공개로 막아둔 계정 -> 400."""
        fake_http(
            github_module,
            {
                settings.GITHUB_TOKEN_URL: json_response({"access_token": "gh-at"}),
                settings.GITHUB_USER_URL: json_response({"id": 42, "login": "octocat"}),
                settings.GITHUB_EMAILS_URL: json_response([]),
            },
        )

        with pytest.raises(OAuthEmailNotFoundError):
            await GitHubOAuthClient().get_user_info("code")

    async def test_token_response_without_access_token(self, fake_http, settings):
        fake_http(
            github_module,
            {
                settings.GITHUB_TOKEN_URL: json_response(
                    {"error": "bad_verification_code"}
                ),
            },
        )

        with pytest.raises(OAuthProviderError):
            await GitHubOAuthClient().get_user_info("code")

    async def test_user_endpoint_http_error(self, fake_http, settings):
        """토큰은 받았지만 user API 가 401 -> 502 로 번역."""
        fake_http(
            github_module,
            {
                settings.GITHUB_TOKEN_URL: json_response({"access_token": "gh-at"}),
                settings.GITHUB_USER_URL: json_response(
                    {"message": "Bad credentials"}, status=401
                ),
            },
        )

        with pytest.raises(OAuthProviderError):
            await GitHubOAuthClient().get_user_info("code")

    async def test_network_error(self, fake_http, settings):
        fake_http(
            github_module,
            {settings.GITHUB_TOKEN_URL: httpx.ReadTimeout("timeout")},
        )

        with pytest.raises(OAuthProviderError):
            await GitHubOAuthClient().get_user_info("code")
