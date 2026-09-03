import json

import secrets
from uuid import uuid4

from typing import Optional, Any

from pydantic import EmailStr

from arq.connections import ArqRedis

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.security import AuthManager
from app.domains.auth.enums import OAuthProvider
from app.domains.auth.model import OAuthAccount

from app.domains.auth.repository import OAuthAccountRepository
from app.domains.auth.exceptions import (
    UserNotFoundError,
    EmailAlreadyExistsError,
    RegisterRequestNotFoundError,
    InvalidVerificationCodeError,
    InvalidCredentialsError,
    InactiveUserError,
    TokenInvalidError,
    TokenExpiredError,
    SessionExpiredError,
    ExpiredCodeOrRequestNotFoundError,
)
from app.domains.auth.schema import (
    LoginRequest,
    OAuthUserInfo,
    RegisterRequest,
    VerifyEmailRequest,
    ResendEmailRequest,
    IssuedTokens
)
from app.domains.user.model import User
from app.domains.user.repository import UserRepository


settings = get_settings()


class AuthService:
    REGISTER_PREFIX = "auth:register"

    def __init__(
        self,
        session: AsyncSession,
        redis: ArqRedis,
        auth_manager: AuthManager,
        users_repo: UserRepository,
        oauth_accounts: OAuthAccountRepository,
    ):
        self.session = session

        self.redis = redis

        self.auth_manager = auth_manager

        self.users_repo = users_repo

        self.oauth_accounts = oauth_accounts


    async def request_register_code(self, data: RegisterRequest):
        email = self.auth_manager.normalize_email(data.email)
        existing_user = await self.users_repo.get_by_email(email)

        if existing_user:
            raise EmailAlreadyExistsError()

        code = self.auth_manager.create_otp_code()

        password_hash = self.auth_manager.hash_password(data.password)
        payload = {
            "code": code,
            "password_hash": password_hash,
            "name": data.name.strip(),
        }

        key = f"{self.REGISTER_PREFIX}:{email}"

        await self.redis.setex(key, 300, json.dumps(payload))

        await self.redis.enqueue_job(
            "send_verification_email",
            email,
            code,
        )


    async def resend_register_code(self, data: ResendEmailRequest):
        email = self.auth_manager.normalize_email(data.email)
        key = f"{self.REGISTER_PREFIX}:{email}"

        raw = await self.redis.get(key)

        if raw is None:
            raise RegisterRequestNotFoundError()

        if isinstance(raw, bytes):
            raw = raw.decode()

        payload = json.loads(raw)

        code = self.auth_manager.create_otp_code()

        payload["code"] = code

        await self.redis.setex(key, 300, json.dumps(payload))

        await self.redis.enqueue_job(
            "send_verification_email",
            email,
            code,
        )


    async def register_user(self, data: VerifyEmailRequest):
        email = self.auth_manager.normalize_email(data.email)
        key = f"{self.REGISTER_PREFIX}:{email}"
        payload = await self._check_otp_code(key)

        if payload["code"] != data.code:
            raise InvalidVerificationCodeError()

        existing_user = await self.users_repo.get_by_email(email)

        if existing_user:
            await self.redis.delete(key)

            raise EmailAlreadyExistsError()

        user = User(
            email=email,
            password_hash=(
                payload["password_hash"]
            ),
            name=payload["name"],
            email_verified=True,
            is_active=True,
        )

        await self.users_repo.create(user)

        await self.session.commit()

        await self.redis.delete(key)


    async def login_user(self, data: LoginRequest) -> IssuedTokens:
        email = self.auth_manager.normalize_email(data.email)

        user = await self._check_user(email, data.password)
        if user is None:
            raise InvalidCredentialsError()

        if not user.is_active:
            raise InactiveUserError()

        return await self._issue_tokens(user.id)


    async def logout(self, refresh_token: str):
        payload = self.auth_manager.decode(
            refresh_token
        )

        if payload.token_type != "refresh":
            raise TokenInvalidError()

        key = f"auth:session:{payload.session_id}"

        session = await self.redis.hgetall(key)

        if not session:
            return

        def read(name: str) -> str | None:
            value = session.get(name)

            if value is None:
                value = session.get(name.encode())

            if isinstance(value, bytes):
                return value.decode()

            return value

        current_refresh_jti = read("refresh_jti")

        if current_refresh_jti != payload.jti:
            raise SessionExpiredError()

        await self.redis.delete(key)


    async def withdraw(self, user: User):
        user.is_active = False

        self.session.add(user)

        await self.session.commit()

        await self.revoke_all_sessions(user.id)


    async def revoke_all_sessions(self, user_id: int):
        index_key = f"auth:user:sessions:{user_id}"

        session_ids = await self.redis.smembers(index_key)

        for session_id in session_ids:
            if isinstance(session_id, bytes):
                session_id = session_id.decode()

            await self.redis.delete(
                f"auth:session:{session_id}"
            )

        await self.redis.delete(index_key)


    async def reissue_token(self, refresh_token: str) -> IssuedTokens:
        payload = self.auth_manager.decode(refresh_token)

        if payload.token_type != "refresh":
            raise TokenInvalidError()

        key = f"auth:session:{payload.session_id}"

        session = await self.redis.hgetall(key)

        if not session:
            raise SessionExpiredError()

        def read(name: str):
            value = session.get(name)

            if value is None:
                value = session.get(
                    name.encode()
                )

            if isinstance(value, bytes):
                return value.decode()

            return value

        session_user_id = read("user_id")
        current_refresh_jti = read("refresh_jti")

        if (
            session_user_id is None
            or int(session_user_id)
            != payload.user_id
        ):
            raise TokenInvalidError()

        if current_refresh_jti != payload.jti:
            raise SessionExpiredError()

        user = await self.users_repo.get_by_id(payload.user_id)

        if user is None:
            raise TokenInvalidError()

        if not user.is_active:
            raise InactiveUserError()

        return await self._issue_tokens(
            user.id,
            payload.session_id,
        )


    async def _issue_tokens(
        self,
        user_id: int,
        session_id: str | None = None,
    ) -> IssuedTokens:
        if session_id is None:
            session_id = str(uuid4())

        access_token, access_jti = self.auth_manager.create_access_token(
            user_id, session_id
        )
        refresh_token, refresh_jti = self.auth_manager.create_refresh_token(
            user_id, session_id
        )

        key = f"auth:session:{session_id}"

        await self.redis.hset(
            key,
            mapping={
                "user_id": str(user_id),
                "access_jti": access_jti,
                "refresh_jti": refresh_jti,
            },
        )
        await self.redis.expire(
            key,
            settings.REFRESH_TOKEN_EXPIRE_DAY
            * 86400,
        )
        await self.redis.sadd(
            f"auth:user:sessions:{user_id}",
            session_id,
        )

        return IssuedTokens(
            access_token=access_token,
            refresh_token=refresh_token,
        )


    async def _check_user(self, email: EmailStr, password: str) -> Optional[User]:
        user = await self.users_repo.get_by_email(email)
        if not user:
            return None

        if user.password_hash is None:
            return None

        if not self.auth_manager.verify_password(
            password,
            user.password_hash
        ):
            return None
        return user


    async def _check_otp_code(self, key: str):
        data = await self.redis.get(key)

        if data is None:
            raise ExpiredCodeOrRequestNotFoundError()

        if isinstance(data, bytes):
            data = data.decode()

        payload = json.loads(data)

        return payload