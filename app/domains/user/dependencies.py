from typing import Annotated

from fastapi import Depends

from app.core.dependencies import SessionDep

from app.domains.user.repository import UserRepository


def get_user_repository(session: SessionDep) -> UserRepository:
    return UserRepository(session)


UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]