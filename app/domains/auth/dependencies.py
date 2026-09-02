from typing import Annotated

from fastapi import Depends

from app.core.dependencies import AuthManagerDep, SessionDep, RedisDep
from app.domains.auth.repository import OAuthAccountRepository
from app.domains.auth.service import AuthService
from app.domains.user.dependencies import UserRepositoryDep


def get_oauth_account_repo(session: SessionDep) -> OAuthAccountRepository:
    return OAuthAccountRepository(session)

AuthAccountRepoDep = Annotated[OAuthAccountRepository, Depends(get_oauth_account_repo)]


async def get_auth_service(
    session: SessionDep,
    auth_manager: AuthManagerDep,
    user_repository: UserRepositoryDep,
    auth_account_repo: AuthAccountRepoDep,
    redis: RedisDep,
) -> AuthService:
    return AuthService(
        session=session,
        redis=redis,
        auth_manager=auth_manager,
        users_repo=user_repository,
        oauth_accounts=auth_account_repo,
    )

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]