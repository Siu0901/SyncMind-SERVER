"""
app/domains/auth/oauth/service.py - OAuthService 단위 테스트.

여기서 검증하는 것
    - state 파라미터(CSRF 방어)가 실제로 검증/소모되는지
    - 소셜 계정 <-> 기존 계정 매핑 규칙 (신규 가입 / 재로그인 / 이메일 충돌)
    - 프론트로 넘길 1회용 티켓 발급

외부 HTTP(구글/깃허브)는 OAuthClient mock 으로 끊는다.
실제 HTTP 호출부 검증은 test_oauth_client.py 에서 따로 한다.
"""

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

from app.domains.auth.enums import OAuthProvider
from app.domains.auth.exceptions import (
    InactiveUserError,
    InvalidOAuthStateError,
    OAuthEmailConflictError,
)
from app.domains.auth.model import OAuthAccount
from app.domains.auth.schema import OAuthUserInfo
from app.domains.user.model import User

pytestmark = pytest.mark.anyio


@pytest.fixture
def google_user_info() -> OAuthUserInfo:
    """구글이 돌려줬다고 가정하는 사용자 정보."""
    return OAuthUserInfo(
        provider=OAuthProvider.GOOGLE,
        provider_user_id="google-uid-1",
        email="social@example.com",
        name="소셜유저",
        profile_image_url="https://cdn.example.com/a.png",
    )


@pytest.fixture
def stored_state(mock_redis):
    """
    redis 에 'state 가 저장돼 있다' 는 상황을 만들어주는 헬퍼.

    handle_callback 은 redis.get(state key) 로 저장된 provider 값을 읽는다.
    """

    def _stored(provider_value: str | bytes = b"google"):
        mock_redis.get = AsyncMock(return_value=provider_value)

    return _stored


# ===========================================================================
# 1. 로그인 URL 생성 (state 발급)
# ===========================================================================
class TestCreateLoginUrl:
    async def test_state_is_stored_with_ttl(
        self, oauth_service, mock_redis, mock_oauth_client, mock_oauth_factory
    ):
        """
        state 를 만들어 redis 에 10분 TTL 로 저장하고,
        같은 state 를 클라이언트의 authorize URL 생성에 넘겨야 한다.
        """
        url = await oauth_service.create_login_url(OAuthProvider.GOOGLE)

        mock_redis.setex.assert_awaited_once()
        key, ttl, value = mock_redis.setex.await_args.args

        assert key.startswith("auth:oauth:state:")
        assert ttl == 600
        assert value == "google"        # 나중에 콜백의 provider 와 대조하기 위한 값

        state = key.removeprefix("auth:oauth:state:")
        assert len(state) >= 32          # token_urlsafe(32) -> 추측 불가능한 길이

        mock_oauth_factory.get.assert_called_once_with(OAuthProvider.GOOGLE)
        mock_oauth_client.create_authorization_url.assert_called_once_with(state)
        assert url == mock_oauth_client.create_authorization_url.return_value

    async def test_state_is_unique_per_request(self, oauth_service, mock_redis):
        """요청마다 다른 state 가 나와야 한다 (재사용되면 CSRF 방어가 무의미)."""
        await oauth_service.create_login_url(OAuthProvider.GOOGLE)
        await oauth_service.create_login_url(OAuthProvider.GOOGLE)

        keys = [call.args[0] for call in mock_redis.setex.await_args_list]
        assert keys[0] != keys[1]


# ===========================================================================
# 2. state 검증
# ===========================================================================
class TestValidateState:
    async def test_unknown_state(self, oauth_service, mock_redis, mock_oauth_client):
        """redis 에 없는 state(만료/위조) -> 401, 외부 토큰 교환까지 가면 안 된다."""
        mock_redis.get = AsyncMock(return_value=None)

        with pytest.raises(InvalidOAuthStateError):
            await oauth_service.handle_callback(
                provider=OAuthProvider.GOOGLE, code="code", state="bad-state"
            )

        mock_oauth_client.get_user_info.assert_not_awaited()

    async def test_provider_mismatch(
        self, oauth_service, mock_redis, stored_state, mock_oauth_client
    ):
        """
        google 로 시작한 state 를 github 콜백에 끼워 넣는 시나리오 -> 401.
        (provider 혼선 공격 방어)
        """
        stored_state(b"google")

        with pytest.raises(InvalidOAuthStateError):
            await oauth_service.handle_callback(
                provider=OAuthProvider.GITHUB, code="code", state="s1"
            )

        mock_oauth_client.get_user_info.assert_not_awaited()

    async def test_state_is_consumed_once(
        self, oauth_service, mock_redis, stored_state, mock_oauth_client,
        google_user_info, mock_user_repo,
    ):
        """state 는 1회용: 검증 직후 삭제돼야 재사용(replay)을 막는다."""
        stored_state(b"google")
        mock_oauth_client.get_user_info = AsyncMock(return_value=google_user_info)
        mock_user_repo.get_by_email.return_value = None

        await oauth_service.handle_callback(
            provider=OAuthProvider.GOOGLE, code="code", state="s1"
        )

        assert mock_redis.delete.await_args_list[0].args[0] == "auth:oauth:state:s1"

    @pytest.mark.parametrize("stored", [b"google", "google"])
    async def test_state_value_accepts_bytes_and_str(
        self, oauth_service, mock_redis, stored_state, mock_oauth_client,
        google_user_info, stored,
    ):
        stored_state(stored)
        mock_oauth_client.get_user_info = AsyncMock(return_value=google_user_info)

        # 예외 없이 끝까지 진행되면 성공
        await oauth_service.handle_callback(
            provider=OAuthProvider.GOOGLE, code="code", state="s1"
        )


# ===========================================================================
# 3. 콜백 전체 흐름 (신규 가입)
# ===========================================================================
class TestHandleCallbackNewUser:
    async def test_creates_user_and_oauth_account(
        self, oauth_service, mock_redis, mock_session, mock_user_repo, mock_oauth_repo,
        mock_oauth_client, stored_state, google_user_info, settings,
    ):
        """
        처음 소셜 로그인하는 유저
          - User 생성 (비밀번호 없음 / 이메일 인증됨)
          - OAuthAccount 생성
          - commit
          - 프론트 콜백 URL + 티켓 반환
        """
        stored_state(b"google")
        mock_oauth_client.get_user_info = AsyncMock(return_value=google_user_info)
        mock_oauth_repo.get_by_provider_identity.return_value = None
        mock_user_repo.get_by_email.return_value = None

        redirect_url = await oauth_service.handle_callback(
            provider=OAuthProvider.GOOGLE, code="auth-code", state="s1"
        )

        # --- 코드 교환은 실제 클라이언트에 위임 ---
        mock_oauth_client.get_user_info.assert_awaited_once_with("auth-code")

        # --- 유저 생성 내용 ---
        created_user: User = mock_user_repo.create.await_args.args[0]
        assert created_user.email == "social@example.com"
        assert created_user.password_hash is None      # 소셜 전용 계정
        assert created_user.email_verified is True     # 공급자가 인증한 이메일
        assert created_user.is_active is True
        assert created_user.profile_image_url == "https://cdn.example.com/a.png"

        # --- 소셜 계정 매핑 ---
        created_account: OAuthAccount = mock_oauth_repo.create.await_args.args[0]
        assert created_account.provider == OAuthProvider.GOOGLE
        assert created_account.provider_user_id == "google-uid-1"
        assert created_account.user_id == created_user.id

        mock_session.commit.assert_awaited_once()

        # --- 리다이렉트 URL: 프론트 콜백 + 1회용 ticket ---
        parsed = urlparse(redirect_url)
        expected = urlparse(settings.FRONTEND_OAUTH_CALLBACK_URL)

        assert (parsed.scheme, parsed.netloc, parsed.path) == (
            expected.scheme,
            expected.netloc,
            expected.path,
        )

        ticket = parse_qs(parsed.query)["ticket"][0]

        # --- 티켓은 redis 에 60초 TTL 로 저장돼 있어야 한다 ---
        ticket_call = [
            call
            for call in mock_redis.setex.await_args_list
            if call.args[0].startswith("auth:oauth:ticket:")
        ][0]
        assert ticket_call.args[0] == f"auth:oauth:ticket:{ticket}"
        assert ticket_call.args[1] == 60
        assert ticket_call.args[2] == str(created_user.id)

    async def test_email_normalized_before_lookup(
        self, oauth_service, mock_user_repo, mock_oauth_client, stored_state
    ):
        """공급자가 대문자 이메일을 줘도 정규화한 뒤 기존 계정을 조회해야 한다."""
        stored_state(b"google")
        mock_oauth_client.get_user_info = AsyncMock(
            return_value=OAuthUserInfo(
                provider=OAuthProvider.GOOGLE,
                provider_user_id="uid",
                email="Social@Example.COM",
                name="소셜유저",
            )
        )
        mock_user_repo.get_by_email.return_value = None

        await oauth_service.handle_callback(
            provider=OAuthProvider.GOOGLE, code="c", state="s1"
        )

        mock_user_repo.get_by_email.assert_awaited_once_with("social@example.com")
        assert mock_user_repo.create.await_args.args[0].email == "social@example.com"

    async def test_email_already_registered_with_password(
        self, oauth_service, mock_user_repo, mock_oauth_repo, mock_oauth_client,
        mock_session, stored_state, google_user_info, make_user,
    ):
        """
        같은 이메일이 이미 '비밀번호 가입' 으로 존재 -> 409.
        계정 자동 병합을 하지 않는 것이 현재 정책이다.
        """
        stored_state(b"google")
        mock_oauth_client.get_user_info = AsyncMock(return_value=google_user_info)
        mock_oauth_repo.get_by_provider_identity.return_value = None
        mock_user_repo.get_by_email.return_value = make_user(email="social@example.com")

        with pytest.raises(OAuthEmailConflictError):
            await oauth_service.handle_callback(
                provider=OAuthProvider.GOOGLE, code="c", state="s1"
            )

        mock_user_repo.create.assert_not_awaited()
        mock_session.commit.assert_not_awaited()

    async def test_commit_failure_rolls_back(
        self, oauth_service, mock_session, mock_user_repo, mock_oauth_client,
        stored_state, google_user_info,
    ):
        """가입 도중 커밋이 실패하면 롤백 후 예외를 그대로 올린다."""
        stored_state(b"google")
        mock_oauth_client.get_user_info = AsyncMock(return_value=google_user_info)
        mock_user_repo.get_by_email.return_value = None
        mock_session.commit.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await oauth_service.handle_callback(
                provider=OAuthProvider.GOOGLE, code="c", state="s1"
            )

        mock_session.rollback.assert_awaited_once()


# ===========================================================================
# 4. 콜백 전체 흐름 (기존 소셜 계정 재로그인)
# ===========================================================================
class TestHandleCallbackExistingUser:
    async def test_returning_user_is_not_recreated(
        self, oauth_service, mock_session, mock_user_repo, mock_oauth_repo,
        mock_oauth_client, stored_state, google_user_info, make_user,
    ):
        """이미 연결된 소셜 계정이면 유저/계정을 다시 만들지 않는다."""
        stored_state(b"google")
        mock_oauth_client.get_user_info = AsyncMock(return_value=google_user_info)
        mock_oauth_repo.get_by_provider_identity.return_value = OAuthAccount(
            id=1,
            user_id=10,
            provider=OAuthProvider.GOOGLE,
            provider_user_id="google-uid-1",
        )
        mock_user_repo.get_by_id.return_value = make_user(user_id=10)

        await oauth_service.handle_callback(
            provider=OAuthProvider.GOOGLE, code="c", state="s1"
        )

        mock_user_repo.get_by_id.assert_awaited_once_with(10)
        mock_user_repo.create.assert_not_awaited()
        mock_oauth_repo.create.assert_not_awaited()
        mock_session.commit.assert_not_awaited()

    async def test_inactive_user_cannot_login(
        self, oauth_service, mock_user_repo, mock_oauth_repo, mock_oauth_client,
        stored_state, google_user_info, make_user,
    ):
        """탈퇴한 계정은 소셜 로그인으로도 되살아나면 안 된다 -> 403."""
        stored_state(b"google")
        mock_oauth_client.get_user_info = AsyncMock(return_value=google_user_info)
        mock_oauth_repo.get_by_provider_identity.return_value = OAuthAccount(
            id=1,
            user_id=10,
            provider=OAuthProvider.GOOGLE,
            provider_user_id="google-uid-1",
        )
        mock_user_repo.get_by_id.return_value = make_user(user_id=10, is_active=False)

        with pytest.raises(InactiveUserError):
            await oauth_service.handle_callback(
                provider=OAuthProvider.GOOGLE, code="c", state="s1"
            )

    async def test_orphan_oauth_account(
        self, oauth_service, mock_user_repo, mock_oauth_repo, mock_oauth_client,
        stored_state, google_user_info,
    ):
        """
        소셜 계정 매핑은 있는데 User 행이 없는 데이터 불일치 상황.

        [현재 구현 확인용] 이 경우 OAuthEmailConflictError(409, "이미 다른 로그인 방식으로
        가입된 이메일입니다") 가 나간다. 의미상 맞는 에러는 아니라서
        (409 이메일 충돌이 아니라 500/401 계열이 맞다) 코드를 고칠 때 이 테스트도 같이 바꿔야 한다.
        """
        stored_state(b"google")
        mock_oauth_client.get_user_info = AsyncMock(return_value=google_user_info)
        mock_oauth_repo.get_by_provider_identity.return_value = OAuthAccount(
            id=1,
            user_id=10,
            provider=OAuthProvider.GOOGLE,
            provider_user_id="google-uid-1",
        )
        mock_user_repo.get_by_id.return_value = None

        with pytest.raises(OAuthEmailConflictError):
            await oauth_service.handle_callback(
                provider=OAuthProvider.GOOGLE, code="c", state="s1"
            )


# ===========================================================================
# 5. 티켓 발급
# ===========================================================================
class TestCreateTicket:
    async def test_ticket_is_random_and_short_lived(self, oauth_service, mock_redis):
        """
        티켓은 URL 쿼리로 프론트에 노출되므로
          - 추측 불가능해야 하고
          - 수명이 아주 짧아야 한다 (60초)
        """
        first = await oauth_service._create_ticket(1)
        second = await oauth_service._create_ticket(1)

        assert first != second
        assert len(first) >= 32

        key, ttl, value = mock_redis.setex.await_args.args
        assert key == f"auth:oauth:ticket:{second}"
        assert ttl == 60
        assert value == "1"
