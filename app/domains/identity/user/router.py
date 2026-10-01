from fastapi import APIRouter

from app.domains.identity.auth.dependencies import (
    CurrentUserDep,
)
from app.domains.identity.user.schema import (
    UserResponse,
)


user_router = APIRouter(
    prefix="/users",
    tags=["users"],
)


@user_router.get(
    "/me",
    response_model=UserResponse,
)
async def get_me(current_user: CurrentUserDep):
    return current_user
