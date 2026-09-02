from fastapi import APIRouter

from app.domains.auth.schema import (
    RegisterRequest,
    VerifyEmailRequest,
    ResendEmailRequest,
)
from app.domains.auth.dependencies import AuthServiceDep

auth_router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)


@auth_router.post("/email/send")
async def send_email(
    data: RegisterRequest,
    service: AuthServiceDep,
):
    await service.request_register_code(data)
    return {"message":"인증 이메일을 전송했습니다."}


@auth_router.post("/email/resend")
async def resend_email(
    data: ResendEmailRequest,
    service: AuthServiceDep,
):
    await service.resend_register_code(data)
    return {"message": "인증 이메일을 재전송했습니다."}


@auth_router.post("/register")
async def register(
    data: VerifyEmailRequest,
    service: AuthServiceDep,
):
    await service.register_user(data)
    return {"message": "회원가입이 완료되었습니다."}