import json

import secrets
from types import CoroutineType

from typing import Optional, Any

from pydantic import BaseModel, EmailStr

from fastapi import HTTPException

from arq.connections import ArqRedis

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.core.security import AuthManager
from app.domains.auth.enums import OAuthProvider
from app.domains.auth.model import OAuthAccount

from app.domains.auth.repository import OAuthAccountRepository
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
            raise HTTPException(
                status_code=409,
                detail="이미 사용 중인 이메일입니다."
            )

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
            raise HTTPException(
                status_code=404,
                detail="진행 중인 회원가입 요청이 없습니다."
            )

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
            raise HTTPException(
                status_code=400,
                detail="인증 코드가 올바르지 않습니다."
            )

        existing_user = await self.users_repo.get_by_email(email)

        if existing_user:
            await self.redis.delete(key)

            raise HTTPException(
                status_code=409,
                detail="이미 사용 중인 이메일 입니다."
            )

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
        if not user:
            raise HTTPException(
                status_code=401,
                detail="이메일 또는 비밀번호가 올바르지 않습니다."
            )

        if not user.is_active:
            raise HTTPException(
                status_code=403,
                detail="비활성화된 계정입니다."
            )

        return await self._issue_tokens(user.id)


    async def logout(self, refresh_token: str):
        payload = self.auth_manager.decode(refresh_token)

        if payload.token_type != "refresh":
            return

        key = (
            f"auth:refresh:"
            f"{payload.user_id}:"
            f"{payload.jti}"
        )
        # access 블랙리스트 해야되는거 아닌가
        await self.redis.delete(key)


    async def withdraw(self, user: User):
        user.is_active = False

        self.session.add(user)

        await self.session.commit()


    async def reissue_token(self, refresh_token: str) -> IssuedTokens:
        payload = self.auth_manager.decode(refresh_token)

        if payload.token_type != "refresh":
            raise HTTPException(
                status_code=401,
                detail="유효하지 않은 토큰입니다.",
            )

        key = (
            f"auth:refresh:"
            f"{payload.user_id}:"
            f"{payload.jti}"
        )

        exists = await self.redis.exists(key)

        if not exists:
            raise HTTPException(
                status_code=401,
                detail="만료되었거나 폐기된 토큰입니다.",
            )

        user = await self.users_repo.get_by_id(payload.user_id)

        if user is None or not user.is_active:
            raise HTTPException(
                status_code=401,
                detail="유효하지 않은 사용자입니다.",
            )

        await self.redis.delete(key)

        return await self._issue_tokens(payload.user_id)


    async def _issue_tokens(self, user_id: int) -> IssuedTokens:
        access_token = self.auth_manager.create_access_token(user_id)
        refresh_token, jti = self.auth_manager.create_refresh_token(user_id)

        key = f"auth:refresh:{user_id}:{jti}"

        await self.redis.setex(
            key,
            settings.REFRESH_TOKEN_EXPIRE_DAY * 86400,
            "1",
        )

        return IssuedTokens(
            access_token=access_token,
            refresh_token=refresh_token,
        )


    async def _check_user(self, email: EmailStr, password: str) -> User | False:
        user = await self.users_repo.get_by_email(email)
        if not user:
            return False
        if not self.auth_manager.verify_password(
                password,
                user.password_hash
        ):
            return False
        return user


    async def _check_otp_code(self, key: str):
        data = await self.redis.get(key)

        if data is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "인증 코드가 만료되었거나 "
                    "회원가입 요청이 존재하지 않습니다."
                )
            )

        if isinstance(data, bytes):
            data = data.decode()

        payload = json.loads(data)

        return payload