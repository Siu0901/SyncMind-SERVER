import logging
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.domains.auth.exceptions import (
    OAuthEmailNotFoundError,
    OAuthProviderError,
)
from app.domains.auth.enums import OAuthProvider
from app.domains.auth.oauth.base import OAuthClient
from app.domains.auth.schema import OAuthUserInfo


settings = get_settings()

logger = logging.getLogger(__name__)


class GoogleOAuthClient(OAuthClient):

    def create_authorization_url(self, state: str) -> str:
        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        }

        return (
            f"{settings.GOOGLE_AUTH_URL}?"
            f"{urlencode(params)}"
        )

    async def get_user_info(self, code: str) -> OAuthUserInfo:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:

                token_response = await client.post(
                    settings.GOOGLE_TOKEN_URL,
                    data={
                        "client_id":
                            settings.GOOGLE_CLIENT_ID,

                        "client_secret":
                            settings.GOOGLE_CLIENT_SECRET,

                        "code": code,

                        "grant_type":
                            "authorization_code",

                        "redirect_uri":
                            settings.GOOGLE_REDIRECT_URI,
                    },
                )

                token_response.raise_for_status()

                token_data = token_response.json()

                access_token = token_data.get("access_token")

                if access_token is None:
                    raise OAuthProviderError("google")

                user_response = await client.get(
                    settings.GOOGLE_USERINFO_URL,
                    headers={
                        "Authorization":
                            f"Bearer {access_token}"
                    },
                )

                user_response.raise_for_status()

                user_data = user_response.json()

        except OAuthProviderError:
            raise

        except httpx.HTTPError as exc:
            logger.exception("Google OAuth request failed")

            raise OAuthProviderError("google")

        email = user_data.get("email")

        if not email:
            raise OAuthEmailNotFoundError()

        return OAuthUserInfo(
            provider=OAuthProvider.GOOGLE,
            provider_user_id=str(user_data["sub"]),
            email=email,
            name=(
                user_data.get("name")
                or email
            ),
            profile_image_url=user_data.get("picture")
        )