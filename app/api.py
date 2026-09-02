from fastapi import APIRouter

from app.domains.auth.router import auth_router


router = APIRouter()

router.include_router(auth_router, tags=["auth"])