import string

from typing import Optional

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import secrets

import jwt
from pwdlib import PasswordHash

from pydantic import BaseModel, EmailStr

from app.core.config import get_settings

from app.domains.auth.exceptions import (
    TokenExpiredError,
    TokenInvalidError,
)


settings = get_settings()


class TokenPayload(BaseModel):
    user_id: int
    token_type: str
    jti: str
    exp: int
    session_id: str


class AuthManager:
    def __init__(self):
        self._hasher = PasswordHash.recommended()

        self.secret = settings.SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM

        self.access_minutes = (
            settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

        self.refresh_days = (
            settings.REFRESH_TOKEN_EXPIRE_DAY
        )

    def hash_password(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify_password(
        self,
        password: str,
        password_hash: str,
    ) -> bool:
        return self._hasher.verify(
            password,
            password_hash,
        )

    @staticmethod
    def create_otp_code() -> str:
        char = string.ascii_letters + string.digits
        otp = ''.join(secrets.choice(char) for _ in range(6))
        return otp

    @staticmethod
    def normalize_email(email: EmailStr) -> EmailStr:
        return email.strip().lower()

    def create_access_token(
        self,
        user_id: int,
        session_id: str,
    ) -> tuple[str, str]:
        now = datetime.now(timezone.utc)

        jti = str(uuid4())

        payload = {
            "sub": str(user_id),
            "type": "access",
            "sid": session_id,
            "jti": jti,
            "iat": now,
            "exp": now + timedelta(
                minutes=self.access_minutes
            ),
        }

        token = jwt.encode(
            payload,
            self.secret,
            algorithm=self.algorithm,
        )

        return token, jti

    def create_refresh_token(
        self,
        user_id: int,
        session_id: str,
    ) -> tuple[str, str]:
        now = datetime.now(timezone.utc)

        jti = str(uuid4())

        payload = {
            "sub": str(user_id),
            "type": "refresh",
            "sid": session_id,
            "jti": jti,
            "iat": now,
            "exp": now + timedelta(
                days=self.refresh_days
            ),
        }

        token = jwt.encode(
            payload,
            self.secret,
            algorithm=self.algorithm,
        )

        return token, jti

    def decode(self, token: str) -> TokenPayload:
        try:
            payload = jwt.decode(
                token,
                self.secret,
                algorithms=self.algorithm,
            )

        except jwt.ExpiredSignatureError:
            raise TokenExpiredError()

        except jwt.InvalidTokenError:
            raise TokenInvalidError()

        try:
            return TokenPayload(
                user_id=int(payload["sub"]),
                token_type=payload["type"],
                session_id=payload["sid"],
                jti=payload["jti"],
                exp=payload["exp"],
            )

        except (KeyError, TypeError, ValueError):
            raise TokenInvalidError()