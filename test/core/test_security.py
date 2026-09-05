import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.security import AuthManager, TokenPayload
from app.domains.auth.exceptions import TokenExpiredError, TokenInvalidError


# 비밀번호 해싱
class TestPassword:
    def test_hash_password_is_not_plaintext(self, auth_manager: AuthManager):
        password = "password123!"

        hashed = auth_manager.hash_password(password)

        assert hashed != password
        assert password not in hashed
        assert hashed.startswith("$argon2")

    def test_verify_password_success(
        self, auth_manager: AuthManager, raw_password: str, hashed_password: str
    ):
        assert auth_manager.verify_password(raw_password, hashed_password) is True

    def test_verify_password_wrong_password(
        self, auth_manager: AuthManager, hashed_password: str
    ):
        assert auth_manager.verify_password("wrong-password", hashed_password) is False

    def test_hash_is_salted(self, auth_manager: AuthManager):
        first = auth_manager.hash_password("same-password")
        second = auth_manager.hash_password("same-password")

        assert first != second
        assert auth_manager.verify_password("same-password", first)
        assert auth_manager.verify_password("same-password", second)


# OTP / 이메일 정규화
class TestOtpAndEmail:
    def test_create_otp_code_length_and_charset(self):
        code = AuthManager.create_otp_code()

        assert len(code) == 6
        assert code.isalnum()

    def test_create_otp_code_is_random(self):
        codes = {AuthManager.create_otp_code() for _ in range(20)}

        assert len(codes) > 1

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("USER@EXAMPLE.COM", "user@example.com"),
            ("  user@example.com  ", "user@example.com"),
            ("MixedCase@Example.Com", "mixedcase@example.com"),
            ("user@example.com", "user@example.com"),
        ],
    )
    def test_normalize_email(self, raw, expected):
        assert AuthManager.normalize_email(raw) == expected


# 토큰 발급
class TestTokenIssue:
    def test_create_access_token_payload(self, auth_manager: AuthManager, settings):
        token, jti = auth_manager.create_access_token(user_id=7, session_id="sid-1")

        decoded = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )

        assert decoded["sub"] == "7"
        assert decoded["type"] == "access"
        assert decoded["sid"] == "sid-1"
        assert decoded["jti"] == jti
        assert decoded["exp"] > decoded["iat"]

    def test_create_refresh_token_payload(self, auth_manager: AuthManager, settings):
        token, jti = auth_manager.create_refresh_token(user_id=7, session_id="sid-1")

        decoded = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )

        assert decoded["type"] == "refresh"
        assert decoded["jti"] == jti

    def test_access_and_refresh_have_different_jti(self, auth_manager: AuthManager):
        _, access_jti = auth_manager.create_access_token(1, "sid")
        _, refresh_jti = auth_manager.create_refresh_token(1, "sid")

        assert access_jti != refresh_jti

    def test_refresh_expires_later_than_access(self, auth_manager: AuthManager, settings):
        access, _ = auth_manager.create_access_token(1, "sid")
        refresh, _ = auth_manager.create_refresh_token(1, "sid")

        access_exp = jwt.decode(
            access, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )["exp"]
        refresh_exp = jwt.decode(
            refresh, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )["exp"]

        assert refresh_exp > access_exp


# 토큰 검증 (decode)
class TestTokenDecode:
    def test_decode_access_token_roundtrip(self, auth_manager: AuthManager):
        token, jti = auth_manager.create_access_token(user_id=42, session_id="sid-42")

        payload = auth_manager.decode(token)

        assert isinstance(payload, TokenPayload)
        assert payload.user_id == 42
        assert payload.token_type == "access"
        assert payload.session_id == "sid-42"
        assert payload.jti == jti

    def test_decode_refresh_token_roundtrip(self, auth_manager: AuthManager):
        token, jti = auth_manager.create_refresh_token(user_id=42, session_id="sid-42")

        payload = auth_manager.decode(token)

        assert payload.token_type == "refresh"
        assert payload.jti == jti

    def test_decode_expired_token(self, auth_manager: AuthManager, settings):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)

        token = jwt.encode(
            {
                "sub": "1",
                "type": "access",
                "sid": "sid",
                "jti": "jti",
                "iat": past - timedelta(minutes=1),
                "exp": past,
            },
            settings.SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(TokenExpiredError):
            auth_manager.decode(token)

    def test_decode_tampered_signature(self, auth_manager: AuthManager):
        token, _ = auth_manager.create_access_token(1, "sid")

        tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")

        with pytest.raises(TokenInvalidError):
            auth_manager.decode(tampered)

    def test_decode_token_signed_with_other_secret(
        self, auth_manager: AuthManager, settings
    ):
        token = jwt.encode(
            {
                "sub": "1",
                "type": "access",
                "sid": "sid",
                "jti": "jti",
                "exp": int(time.time()) + 600,
            },
            "totally-different-secret",
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(TokenInvalidError):
            auth_manager.decode(token)

    def test_decode_garbage_string(self, auth_manager: AuthManager):
        with pytest.raises(TokenInvalidError):
            auth_manager.decode("this-is-not-a-jwt")

    @pytest.mark.parametrize(
        "claims",
        [
            {"type": "access", "sid": "s", "jti": "j"},              # sub 없음
            {"sub": "1", "sid": "s", "jti": "j"},                     # type 없음
            {"sub": "1", "type": "access", "jti": "j"},               # sid 없음
            {"sub": "1", "type": "access", "sid": "s"},               # jti 없음
            {"sub": "not-a-number", "type": "access", "sid": "s", "jti": "j"},  # sub 형변환 실패
        ],
    )
    def test_decode_missing_or_broken_claims(
        self, auth_manager: AuthManager, settings, claims
    ):
        payload = {**claims, "exp": int(time.time()) + 600}

        token = jwt.encode(
            payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM
        )

        with pytest.raises(TokenInvalidError):
            auth_manager.decode(token)