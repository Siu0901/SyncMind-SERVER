from pydantic import BaseModel, EmailStr, Field

from app.domains.auth.enums import OAuthProvider


class RegisterRequest(BaseModel):
    email: EmailStr

    password: str = Field(
        min_length=8,
        max_length=128,
    )

    name: str = Field(
        min_length=1,
        max_length=100,
    )


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class VerifyEmailRequest(BaseModel):
    token: str


class ResendEmailRequest(BaseModel):
    email: EmailStr


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class OAuthTicketRequest(BaseModel):
    ticket: str


class OAuthUserInfo(BaseModel):
    provider: OAuthProvider

    provider_user_id: str

    email: EmailStr

    name: str

    profile_image_url: str | None = None