import jwt

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
    SessionExpiredError,
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
    redis: RedisDep,
) -> User:
    if credentials is None:
        raise TokenInvalidError()

    payload = auth_manager.decode(
        credentials.credentials,
    )

    if payload.token_type != "access":
        raise TokenInvalidError()

    key = f"auth:session:{payload.session_id}"

    session = await redis.hgetall(key)

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
    current_access_jti = read("access_jti")

    if session_user_id is None:
        raise SessionExpiredError()

    if int(session_user_id) != payload.user_id:
        raise TokenInvalidError()

    if current_access_jti != payload.jti:
        raise SessionExpiredError()

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