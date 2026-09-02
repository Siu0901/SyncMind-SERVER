import json

import secrets
from pydantic import BaseModel

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
    ResendEmailRequest
)
from app.domains.user.model import User
from app.domains.user.repository import UserRepository


settings = get_settings()


class IssuedTokens(BaseModel):
    access_token: str
    refresh_token: str


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
        existing_user = await self.users_repo.get_by_email(data.email)

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

        key = f"{self.REGISTER_PREFIX}:{data.email}"

        await self.redis.setex(key, 300, json.dumps(payload))

        await self.redis.enqueue_job(
            "send_verification_email",
            data.email,
            code,
        )


    async def resend_register_code(self, data: ResendEmailRequest):
        key = f"{self.REGISTER_PREFIX}:{data.email}"

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
            data.email,
            code,
        )


    async def register_user(self, data: VerifyEmailRequest):
        key = f"{self.REGISTER_PREFIX}:{data.email}"
        payload = await self._check_otp_code(key)

        if payload["code"] != data.code:
            raise HTTPException(
                status_code=400,
                detail="인증 코드가 올바르지 않습니다."
            )

        existing_user = await self.users_repo.get_by_email(data.email)

        if existing_user:
            await self.redis.delete(key)

            raise HTTPException(
                status_code=409,
                detail="이미 사용 중인 이메일 입니다."
            )

        user = User(
            email=data.email,
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