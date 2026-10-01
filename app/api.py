from fastapi import APIRouter

from app.domains.identity.auth.router import auth_router
from app.domains.identity.user.router import user_router
from app.domains.workspace.router import workspace_router
from app.domains.knowledge.router import document_router


router = APIRouter()

router.include_router(auth_router, tags=["auth"])
router.include_router(user_router, tags=["users"])
router.include_router(workspace_router, tags=["workspaces"])
router.include_router(document_router, tags=["documents"])