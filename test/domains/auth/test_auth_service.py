"""
app/domains/auth/service.py - AuthService 단위 테스트.

전략
    - redis / session / UserRepository 는 mock (외부 I/O 제거)
    - AuthManager 는 진짜 객체 (해시·JWT 는 실제 동작을 검증해야 의미가 있음)
    - 각 메서드의 "모든 분기"를 하나씩 케이스로 만든다.

주의
    conftest 의 mock_redis 는 조회 계열 기본값이 '없음'(None / {} / set()) 이다.
    데이터가 있어야 하는 테스트에서는 각자 return_value 를 명시적으로 세팅한다.
"""

import json

import pytest

from app.domains.auth.exceptions import (
    EmailAlreadyExistsError,
    ExpiredCodeOrRequestNotFoundError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidVerificationCodeError,
    OAuthTicketInvalidError,
    RegisterRequestNotFoundError,
    SessionExpiredError,
    TokenInvalidError,
)
from app.domains.auth.schema import (
    IssuedTokens,
    LoginRequest,
    RegisterRequest,
    ResendEmailRequest,
    VerifyEmailRequest,
)
from app.domains.user.model import User

# 이 모듈의 async 테스트는 anyio 플러그인이 돌린다. (conftest 상단 주석 참고)
pytestmark = pytest.mark.anyio


# ===========================================================================
# 1. 회원가입 인증코드 발송
# ===========================================================================
class TestRequestRegisterCode:
    async def test_success(self, auth_service, mock_redis, mock_user_repo, auth_manager):
        """정상 요청: redis 에 5분 TTL 로 저장 + 메일 발송 잡 enqueue."""
        mock_user_repo.get_by_email.return_value = None

        data = RegisterRequest(
            email="newuser@example.com",
            password="password123!",
            name="테스터",
        )

        await auth_service.request_register_code(data)

        # --- redis 저장 검증 ---
        mock_redis.setex.assert_awaited_once()
        key, ttl, raw = mock_redis.setex.await_args.args

        assert key == "auth:register:newuser@example.com"
        assert ttl == 300  # 인증코드 유효시간 5분

        payload = json.loads(raw)
        assert set(payload) == {"code", "password_hash", "name"}
        assert len(payload["code"]) == 6
        assert payload["name"] == "테스터"

        # --- 평문 비밀번호가 redis 에 저장되면 안 된다 (보안 회귀 방지) ---
        assert "password123!" not in raw
        assert auth_manager.verify_password("password123!", payload["password_hash"])

        # --- 메일 발송 잡 검증: redis 에 저장한 코드와 같은 코드를 보내야 한다 ---
        mock_redis.enqueue_job.assert_awaited_once_with(
            "send_verification_email",
            "newuser@example.com",
            payload["code"],
        )

    async def test_email_is_normalized(self, auth_service, mock_redis, mock_user_repo):
        """대문자/공백이 섞인 이메일도 소문자로 정규화된 키를 써야 한다."""
        mock_user_repo.get_by_email.return_value = None

        data = RegisterRequest(
            email="  NewUser@Example.COM  ",
            password="password123!",
            name="테스터",
        )

        await auth_service.request_register_code(data)

        mock_user_repo.get_by_email.assert_awaited_once_with("newuser@example.com")
        assert mock_redis.setex.await_args.args[0] == "auth:register:newuser@example.com"

    async def test_name_is_stripped(self, auth_service, mock_redis, mock_user_repo):
        mock_user_repo.get_by_email.return_value = None

        data = RegisterRequest(
            email="a@example.com",
            password="password123!",
            name="  테스터  ",
        )

        await auth_service.request_register_code(data)

        payload = json.loads(mock_redis.setex.await_args.args[2])
        assert payload["name"] == "테스터"

    async def test_duplicate_email(self, auth_service, mock_redis, mock_user_repo, make_user):
        """이미 가입된 이메일 -> 409. redis 를 건드리지 않고 즉시 중단해야 한다."""
        mock_user_repo.get_by_email.return_value = make_user(email="exists@example.com")

        data = RegisterRequest(
            email="exists@example.com",
            password="password123!",
            name="테스터",
        )

        with pytest.raises(EmailAlreadyExistsError):
            await auth_service.request_register_code(data)

        mock_redis.setex.assert_not_awaited()
        mock_redis.enqueue_job.assert_not_awaited()


# ===========================================================================
# 2. 인증코드 재발송
# ===========================================================================
class TestResendRegisterCode:
    async def test_no_pending_request(self, auth_service, mock_redis):
        """진행 중인 가입 요청이 없으면 404."""
        mock_redis.get.return_value = None

        with pytest.raises(RegisterRequestNotFoundError):
            await auth_service.resend_register_code(
                ResendEmailRequest(email="nobody@example.com")
            )

        mock_redis.enqueue_job.assert_not_awaited()

    @pytest.mark.parametrize("as_bytes", [False, True])
    async def test_success_keeps_password_and_name(
        self, auth_service, mock_redis, register_payload, as_bytes
    ):
        """
        재발송 시 코드만 새로 만들고 password_hash / name 은 유지해야 한다.
        redis 클라이언트 설정에 따라 str/bytes 가 모두 올 수 있어 두 경우를 검증한다.
        """
        mock_redis.get.return_value = register_payload(
            code="OLD123",
            password_hash="preserved-hash",
            name="보존되는이름",
            as_bytes=as_bytes,
        )

        await auth_service.resend_register_code(
            ResendEmailRequest(email="user@example.com")
        )

        key, ttl, raw = mock_redis.setex.await_args.args
        payload = json.loads(raw)

        assert key == "auth:register:user@example.com"
        assert ttl == 300                                # TTL 도 갱신된다
        assert payload["password_hash"] == "preserved-hash"
        assert payload["name"] == "보존되는이름"

        # 새로 저장한 코드와 메일로 보내는 코드가 반드시 일치해야 한다
        mock_redis.enqueue_job.assert_awaited_once_with(
            "send_verification_email",
            "user@example.com",
            payload["code"],
        )


# ===========================================================================
# 3. 회원가입 완료 (코드 검증)
# ===========================================================================
class TestRegisterUser:
    async def test_expired_or_missing_request(self, auth_service, mock_redis):
        """redis 에 요청이 없으면(만료 포함) 400."""
        mock_redis.get.return_value = None

        with pytest.raises(ExpiredCodeOrRequestNotFoundError):
            await auth_service.register_user(
                VerifyEmailRequest(email="user@example.com", code="abc123")
            )

    async def test_wrong_code(self, auth_service, mock_redis, register_payload):
        """코드 불일치 -> 400, 유저 생성/커밋은 일어나지 않는다."""
        mock_redis.get.return_value = register_payload(code="RIGHT1")

        with pytest.raises(InvalidVerificationCodeError):
            await auth_service.register_user(
                VerifyEmailRequest(email="user@example.com", code="WRONG1")
            )

        mock_redis.delete.assert_not_awaited()

    async def test_email_taken_in_the_meantime(
        self, auth_service, mock_redis, mock_user_repo, register_payload, make_user
    ):
        """
        코드 발송 ~ 검증 사이에 같은 이메일로 가입이 끝난 경쟁 상황.
        409 를 던지면서 대기중인 요청 키는 정리해야 한다.
        """
        mock_redis.get.return_value = register_payload(code="abc123")
        mock_user_repo.get_by_email.return_value = make_user()

        with pytest.raises(EmailAlreadyExistsError):
            await auth_service.register_user(
                VerifyEmailRequest(email="user@example.com", code="abc123")
            )

        mock_redis.delete.assert_awaited_once_with("auth:register:user@example.com")
        mock_user_repo.create.assert_not_awaited()

    async def test_success(
        self, auth_service, mock_redis, mock_session, mock_user_repo, register_payload
    ):
        """정상 가입: User 생성 -> commit -> 대기 키 삭제."""
        mock_redis.get.return_value = register_payload(
            code="abc123",
            password_hash="stored-hash",
            name="테스터",
        )
        mock_user_repo.get_by_email.return_value = None

        await auth_service.register_user(
            VerifyEmailRequest(email="USER@example.com", code="abc123")
        )

        mock_user_repo.create.assert_awaited_once()
        created: User = mock_user_repo.create.await_args.args[0]

        assert created.email == "user@example.com"        # 정규화된 이메일로 저장
        assert created.password_hash == "stored-hash"     # redis 에 있던 해시 재사용
        assert created.name == "테스터"
        assert created.email_verified is True             # 코드 검증을 통과했으므로 True
        assert created.is_active is True

        mock_session.commit.assert_awaited_once()
        mock_redis.delete.assert_awaited_once_with("auth:register:user@example.com")


# ===========================================================================
# 4. 로그인
# ===========================================================================
class TestLogin:
    async def test_unknown_email(self, auth_service, mock_user_repo, raw_password):
        """존재하지 않는 이메일 -> 401 (계정 존재 여부를 노출하지 않기 위해 동일 에러)."""
        mock_user_repo.get_by_email.return_value = None

        with pytest.raises(InvalidCredentialsError):
            await auth_service.login_user(
                LoginRequest(email="nobody@example.com", password=raw_password)
            )

    async def test_wrong_password(self, auth_service, mock_user_repo, make_user):
        mock_user_repo.get_by_email.return_value = make_user()

        with pytest.raises(InvalidCredentialsError):
            await auth_service.login_user(
                LoginRequest(email="user@example.com", password="wrong-password")
            )

    async def test_oauth_only_user_cannot_password_login(
        self, auth_service, mock_user_repo, make_user, raw_password
    ):
        """
        소셜 가입 유저는 password_hash 가 None 이다.
        이 경우 verify_password 를 타지 않고 401 로 끊어야 한다. (None 해시 검증은 예외를 던짐)
        """
        mock_user_repo.get_by_email.return_value = make_user(password_hash=None)

        with pytest.raises(InvalidCredentialsError):
            await auth_service.login_user(
                LoginRequest(email="user@example.com", password=raw_password)
            )

    async def test_inactive_user(
        self, auth_service, mock_user_repo, make_user, raw_password
    ):
        """탈퇴(비활성) 계정 -> 403. 비밀번호가 맞아도 통과시키면 안 된다."""
        mock_user_repo.get_by_email.return_value = make_user(is_active=False)

        with pytest.raises(InactiveUserError):
            await auth_service.login_user(
                LoginRequest(email="user@example.com", password=raw_password)
            )

    async def test_success_issues_tokens_and_session(
        self, auth_service, mock_redis, mock_user_repo, make_user, raw_password,
        auth_manager, settings,
    ):
        """로그인 성공: 토큰 쌍 발급 + redis 세션 해시 생성 + 유저별 세션 인덱스 등록."""
        mock_user_repo.get_by_email.return_value = make_user(user_id=7)

        tokens = await auth_service.login_user(
            LoginRequest(email="user@example.com", password=raw_password)
        )

        assert isinstance(tokens, IssuedTokens)
        assert tokens.token_type == "bearer"

        access = auth_manager.decode(tokens.access_token)
        refresh = auth_manager.decode(tokens.refresh_token)

        assert access.user_id == 7
        assert access.token_type == "access"
        assert refresh.token_type == "refresh"
        # 같은 로그인이므로 세션 id 는 공유, jti 는 서로 달라야 한다
        assert access.session_id == refresh.session_id
        assert access.jti != refresh.jti

        # --- 세션 해시 ---
        mock_redis.hset.assert_awaited_once()
        session_key = mock_redis.hset.await_args.args[0]
        mapping = mock_redis.hset.await_args.kwargs["mapping"]

        assert session_key == f"auth:session:{access.session_id}"
        assert mapping == {
            "user_id": "7",
            "access_jti": access.jti,
            "refresh_jti": refresh.jti,
        }

        # --- TTL 은 refresh 토큰 수명과 동일 ---
        mock_redis.expire.assert_awaited_once_with(
            session_key, settings.REFRESH_TOKEN_EXPIRE_DAY * 86400
        )

        # --- 전체 로그아웃(revoke) 을 위한 세션 인덱스 ---
        mock_redis.sadd.assert_awaited_once_with(
            "auth:user:sessions:7", access.session_id
        )


# ===========================================================================
# 5. 로그아웃
# ===========================================================================
class TestLogout:
    async def test_access_token_rejected(self, auth_service, auth_manager):
        """access 토큰으로 로그아웃 시도 -> 401 (refresh 만 허용)."""
        access_token, _ = auth_manager.create_access_token(1, "sid")

        with pytest.raises(TokenInvalidError):
            await auth_service.logout(access_token)

    async def test_missing_session_is_noop(self, auth_service, mock_redis, auth_manager):
        """
        세션이 이미 사라진 경우 조용히 종료한다. (현재 구현 동작)
        reissue 는 같은 상황에서 401 을 던지므로 의도적인 비대칭인지 확인 필요.
        """
        refresh_token, _ = auth_manager.create_refresh_token(1, "sid")
        mock_redis.hgetall.return_value = {}

        result = await auth_service.logout(refresh_token)

        assert result is None
        mock_redis.delete.assert_not_awaited()

    async def test_rotated_token_rejected(
        self, auth_service, mock_redis, auth_manager, redis_session_value
    ):
        """
        이미 재발급되어 회전된(구버전) refresh 토큰 -> 401.
        세션에 저장된 refresh_jti 와 다르면 무효 처리한다.
        """
        refresh_token, _ = auth_manager.create_refresh_token(1, "sid")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=1, access_jti="a", refresh_jti="다른-jti"
        )

        with pytest.raises(SessionExpiredError):
            await auth_service.logout(refresh_token)

        mock_redis.delete.assert_not_awaited()

    @pytest.mark.parametrize("as_bytes", [False, True])
    async def test_success(
        self, auth_service, mock_redis, auth_manager, redis_session_value, as_bytes
    ):
        """정상 로그아웃 -> 세션 키 삭제. (hgetall 이 str/bytes 어느 쪽이든 동작해야 함)"""
        refresh_token, refresh_jti = auth_manager.create_refresh_token(1, "sid-logout")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=1, access_jti="a", refresh_jti=refresh_jti, as_bytes=as_bytes
        )

        await auth_service.logout(refresh_token)

        mock_redis.delete.assert_awaited_once_with("auth:session:sid-logout")


# ===========================================================================
# 6. 토큰 재발급
# ===========================================================================
class TestReissueToken:
    async def test_access_token_rejected(self, auth_service, auth_manager):
        access_token, _ = auth_manager.create_access_token(1, "sid")

        with pytest.raises(TokenInvalidError):
            await auth_service.reissue_token(access_token)

    async def test_session_not_found(self, auth_service, mock_redis, auth_manager):
        """세션이 없으면(만료/강제 로그아웃) 401."""
        refresh_token, _ = auth_manager.create_refresh_token(1, "sid")
        mock_redis.hgetall.return_value = {}

        with pytest.raises(SessionExpiredError):
            await auth_service.reissue_token(refresh_token)

    async def test_user_id_mismatch(
        self, auth_service, mock_redis, auth_manager, redis_session_value
    ):
        """토큰의 sub 와 세션의 user_id 가 다르면 토큰 위조로 간주."""
        refresh_token, refresh_jti = auth_manager.create_refresh_token(1, "sid")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=999, access_jti="a", refresh_jti=refresh_jti
        )

        with pytest.raises(TokenInvalidError):
            await auth_service.reissue_token(refresh_token)

    async def test_reused_old_refresh_token(
        self, auth_service, mock_redis, auth_manager, redis_session_value
    ):
        """
        회전된 옛 refresh 토큰 재사용 -> 401.
        refresh token rotation 의 핵심 방어라 반드시 지켜져야 한다.
        """
        refresh_token, _ = auth_manager.create_refresh_token(1, "sid")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=1, access_jti="a", refresh_jti="최신-jti"
        )

        with pytest.raises(SessionExpiredError):
            await auth_service.reissue_token(refresh_token)

    async def test_user_deleted(
        self, auth_service, mock_redis, mock_user_repo, auth_manager, redis_session_value
    ):
        refresh_token, refresh_jti = auth_manager.create_refresh_token(1, "sid")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=1, access_jti="a", refresh_jti=refresh_jti
        )
        mock_user_repo.get_by_id.return_value = None

        with pytest.raises(TokenInvalidError):
            await auth_service.reissue_token(refresh_token)

    async def test_inactive_user(
        self, auth_service, mock_redis, mock_user_repo, auth_manager,
        redis_session_value, make_user,
    ):
        refresh_token, refresh_jti = auth_manager.create_refresh_token(1, "sid")
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=1, access_jti="a", refresh_jti=refresh_jti
        )
        mock_user_repo.get_by_id.return_value = make_user(is_active=False)

        with pytest.raises(InactiveUserError):
            await auth_service.reissue_token(refresh_token)

    async def test_success_reuses_session_and_rotates_jti(
        self, auth_service, mock_redis, mock_user_repo, auth_manager,
        redis_session_value, make_user,
    ):
        """
        재발급 성공 조건
          - 세션 id 는 그대로 유지 (같은 로그인 세션)
          - access/refresh jti 는 새 값으로 회전
          - 세션 해시가 새 jti 로 덮어써짐
        """
        old_refresh_token, old_refresh_jti = auth_manager.create_refresh_token(
            5, "sid-keep"
        )
        mock_redis.hgetall.return_value = redis_session_value(
            user_id=5, access_jti="old-access-jti", refresh_jti=old_refresh_jti
        )
        mock_user_repo.get_by_id.return_value = make_user(user_id=5)

        tokens = await auth_service.reissue_token(old_refresh_token)

        new_access = auth_manager.decode(tokens.access_token)
        new_refresh = auth_manager.decode(tokens.refresh_token)

        assert new_access.session_id == "sid-keep"
        assert new_refresh.session_id == "sid-keep"
        assert new_refresh.jti != old_refresh_jti
        assert new_access.jti != "old-access-jti"

        mapping = mock_redis.hset.await_args.kwargs["mapping"]
        assert mock_redis.hset.await_args.args[0] == "auth:session:sid-keep"
        assert mapping == {
            "user_id": "5",
            "access_jti": new_access.jti,
            "refresh_jti": new_refresh.jti,
        }


# ===========================================================================
# 7. OAuth 티켓 교환
# ===========================================================================
class TestExchangeOAuthTicket:
    async def test_invalid_ticket(self, auth_service, mock_redis):
        mock_redis.get.return_value = None

        with pytest.raises(OAuthTicketInvalidError):
            await auth_service.exchange_oauth_ticket("no-such-ticket")

    async def test_ticket_is_single_use(
        self, auth_service, mock_redis, mock_user_repo, make_user
    ):
        """티켓은 1회용: 조회 직후 삭제돼야 재사용 공격을 막을 수 있다."""
        mock_redis.get.return_value = b"3"
        mock_user_repo.get_by_id.return_value = make_user(user_id=3)

        await auth_service.exchange_oauth_ticket("ticket-abc")

        mock_redis.delete.assert_awaited_once_with("auth:oauth:ticket:ticket-abc")

    async def test_ticket_consumed_even_when_user_missing(
        self, auth_service, mock_redis, mock_user_repo
    ):
        """유저 조회에 실패해도 티켓은 이미 소모된 상태여야 한다."""
        mock_redis.get.return_value = b"3"
        mock_user_repo.get_by_id.return_value = None

        with pytest.raises(TokenInvalidError):
            await auth_service.exchange_oauth_ticket("ticket-abc")

        mock_redis.delete.assert_awaited_once_with("auth:oauth:ticket:ticket-abc")

    async def test_inactive_user(self, auth_service, mock_redis, mock_user_repo, make_user):
        mock_redis.get.return_value = b"3"
        mock_user_repo.get_by_id.return_value = make_user(user_id=3, is_active=False)

        with pytest.raises(InactiveUserError):
            await auth_service.exchange_oauth_ticket("ticket-abc")

    @pytest.mark.parametrize("stored", [b"3", "3"])
    async def test_success(
        self, auth_service, mock_redis, mock_user_repo, make_user, auth_manager, stored
    ):
        """redis 가 bytes/str 어느 쪽을 돌려줘도 user_id 파싱이 되어야 한다."""
        mock_redis.get.return_value = stored
        mock_user_repo.get_by_id.return_value = make_user(user_id=3)

        tokens = await auth_service.exchange_oauth_ticket("ticket-abc")

        assert auth_manager.decode(tokens.access_token).user_id == 3
        mock_redis.hset.assert_awaited_once()


# ===========================================================================
# 8. 회원 탈퇴 / 세션 전체 무효화
# ===========================================================================
class TestWithdrawAndRevoke:
    async def test_withdraw_deactivates_and_revokes(
        self, auth_service, mock_session, mock_redis, make_user
    ):
        """
        탈퇴는 soft delete: is_active=False 로 바꾸고 모든 세션을 날린다.
        (계정 행 자체를 삭제하지 않으므로 재로그인은 InactiveUserError 로 막힌다)
        """
        user = make_user(user_id=9)
        mock_redis.smembers.return_value = {b"sid-1", "sid-2"}

        await auth_service.withdraw(user)

        assert user.is_active is False
        mock_session.add.assert_called_once_with(user)
        mock_session.commit.assert_awaited_once()

        deleted = {call.args[0] for call in mock_redis.delete.await_args_list}
        assert deleted == {
            "auth:session:sid-1",
            "auth:session:sid-2",
            "auth:user:sessions:9",
        }

    async def test_revoke_all_sessions_with_no_sessions(
        self, auth_service, mock_redis
    ):
        """활성 세션이 없어도 인덱스 키는 정리한다."""
        mock_redis.smembers.return_value = set()

        await auth_service.revoke_all_sessions(9)

        mock_redis.delete.assert_awaited_once_with("auth:user:sessions:9")


# ===========================================================================
# 9. 내부 헬퍼
# ===========================================================================
class TestInternalHelpers:
    async def test_check_otp_code_decodes_bytes(
        self, auth_service, mock_redis, register_payload
    ):
        mock_redis.get.return_value = register_payload(code="X1Y2Z3", as_bytes=True)

        payload = await auth_service._check_otp_code("auth:register:user@example.com")

        assert payload["code"] == "X1Y2Z3"

    async def test_issue_tokens_generates_new_session_id_when_omitted(
        self, auth_service, mock_redis, auth_manager
    ):
        """session_id 를 넘기지 않으면 새 UUID 세션이 생성된다 (= 새 로그인)."""
        first = await auth_service._issue_tokens(1)
        second = await auth_service._issue_tokens(1)

        assert (
            auth_manager.decode(first.access_token).session_id
            != auth_manager.decode(second.access_token).session_id
        )
        assert mock_redis.sadd.await_count == 2
        # 세션 인덱스는 항상 유저 단위 키에 쌓인다
        assert mock_redis.sadd.await_args.args[0] == "auth:user:sessions:1"
        assert mock_redis.hset.await_args.kwargs["mapping"]["user_id"] == "1"
        assert mock_redis.expire.await_args.args[0].startswith("auth:session:")
