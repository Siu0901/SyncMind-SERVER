from typing import Annotated

from fastapi import Depends

from arq import ArqRedis
from qdrant_client.grpc import Qdrant
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import AuthManager
from app.core.database import get_session, get_worker_session
from app.core.redis import get_redis
from app.core.qdrant import get_qdrant
from app.core.s3 import S3Client


def get_auth_manager() -> AuthManager:
    return AuthManager()

def get_s3_client() -> S3Client:
    return S3Client()


AuthManagerDep = Annotated[AuthManager, Depends(get_auth_manager)]

SessionDep = Annotated[AsyncSession, Depends(get_session)]

RedisDep = Annotated[ArqRedis, Depends(get_redis)]

QdrantDep = Annotated[Qdrant, Depends(get_qdrant)]

WorkerSessionDep = Annotated[AsyncSession, Depends(get_worker_session)]

S3ClientDep = Annotated[S3Client, Depends(get_s3_client)]