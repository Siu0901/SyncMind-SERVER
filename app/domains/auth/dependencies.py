from typing import Annotated, Optional

from fastapi import Depends
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.core.dependencies import AuthManagerDep, SessionDep, RedisDep
from app.domains.auth.repository import OAuthAccountRepository
from app.domains.auth.service import AuthService
from app.domains.auth.exceptions import (
    InactiveUserError,
    TokenInvalidError,
)
from app.domains.user.dependencies import UserRepositoryDep
from app.domains.user.model import User


bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials],
        Depends(bearer_scheme),
    ],
    auth_manager: AuthManagerDep,
    user_repository: UserRepositoryDep,
) -> User:
    if credentials is None:
        raise TokenInvalidError()

    token = credentials.credentials

    try:
        payload = auth_manager.decode(token)
    except Exception as exc:
        raise TokenInvalidError() from exc

    if payload.token_type != "access":
        raise TokenInvalidError()

    user = await user_repository.get_by_id(payload.user_id)

    if user is None:
        raise TokenInvalidError()

    if not user.is_active:
        raise InactiveUserError()

    return user


CurrentUserDep = Annotated[User,Depends(get_current_user)]


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