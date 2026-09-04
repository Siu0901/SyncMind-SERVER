from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from app.domains.auth.schema import (
    LoginRequest,
    RegisterRequest,
    VerifyEmailRequest,
    ResendEmailRequest,
    IssuedTokens,
    RefreshTokenRequest,
)
from app.domains.auth.enums import OAuthProvider
from app.domains.auth.schema import OAuthTicketRequest
from app.domains.auth.dependencies import (
    AuthServiceDep,
    OAuthServiceDep,
)

auth_router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)


# ======= auth =======
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


@auth_router.post("/login")
async def login(
    data: LoginRequest,
    service: AuthServiceDep,
) -> IssuedTokens:
    return await service.login_user(data)


@auth_router.post("/logout") # 엑세스 토큰 관련해서 봐보자
async def logout(
    token: RefreshTokenRequest,
    service: AuthServiceDep
):
    await service.logout(token.refresh_token)
    return {"message": "로그아웃 성공!"}


@auth_router.post("/reissue")
async def reissue(
    token: RefreshTokenRequest,
    service: AuthServiceDep,
) -> IssuedTokens:
    return await service.reissue_token(token.refresh_token)


# ======= oauth =======
@auth_router.get("/oauth/{provider}/login")
async def oauth_login(
    provider: OAuthProvider,
    service: OAuthServiceDep,
):
    url = await service.create_login_url(provider)

    return RedirectResponse(url=url)


@auth_router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: OAuthProvider,
    code: str,
    state: str,
    service: OAuthServiceDep,
):
    redirect_url = (
        await service.handle_callback(
            provider=provider,
            code=code,
            state=state,
        )
    )

    #return RedirectResponse(url=redirect_url)
    return {"wow": redirect_url}


@auth_router.post("/oauth/exchange")
async def oauth_exchange(
    data: OAuthTicketRequest,
    service: AuthServiceDep,
):
    return await service.exchange_oauth_ticket(data.ticket)