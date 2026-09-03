from fastapi import APIRouter

from app.domains.auth.router import auth_router
from app.domains.user.router import user_router


router = APIRouter()

router.include_router(auth_router, tags=["auth"])
router.include_router(user_router, tags=["users"])