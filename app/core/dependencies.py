from typing import Annotated

from fastapi import Depends

from arq import ArqRedis
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import AuthManager
from app.core.database import get_session, get_worker_session
from app.core.redis import get_redis


def get_auth_manager() -> AuthManager:
    return AuthManager()

AuthManagerDep = Annotated[AuthManager, Depends(get_auth_manager)]

SessionDep = Annotated[AsyncSession, Depends(get_session)]

RedisDep = Annotated[ArqRedis, Depends(get_redis)]

WorkerSessionDep = Annotated[AsyncSession, Depends(get_worker_session)]