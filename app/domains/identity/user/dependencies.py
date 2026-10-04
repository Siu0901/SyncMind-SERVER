from typing import Annotated

from fastapi import Depends

from app.core.dependencies import SessionDep

from app.domains.identity.user.repository import UserRepository
from app.domains.identity.user.service import UserQueryService


def get_user_repository(session: SessionDep) -> UserRepository:
    return UserRepository(session)

UserRepositoryDep = Annotated[
    UserRepository,
    Depends(get_user_repository)
]


def get_user_query_service(
    repository: UserRepositoryDep,
) -> UserQueryService:
    return UserQueryService(repository)

UserQueryServiceDep = Annotated[
    UserQueryService,
    Depends(get_user_query_service),
]
