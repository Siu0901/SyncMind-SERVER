from typing import Optional

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


class GitHubOAuthClient(OAuthClient):

    def create_authorization_url(self, state: str,) -> str:
        params = {
            "client_id": settings.GITHUB_CLIENT_ID,

            "redirect_uri": settings.GITHUB_REDIRECT_URI,

            "scope": "read:user user:email",

            "state": state,
        }

        return (
            f"{settings.GITHUB_AUTH_URL}?"
            f"{urlencode(params)}"
        )

    async def get_user_info(self, code: str) -> OAuthUserInfo:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                token_response = await client.post(
                    settings.GITHUB_TOKEN_URL,
                    headers={
                        "Accept": "application/json",
                    },
                    data={
                        "client_id":
                            settings.GITHUB_CLIENT_ID,

                        "client_secret":
                            settings.GITHUB_CLIENT_SECRET,

                        "code": code,

                        "redirect_uri":
                            settings.GITHUB_REDIRECT_URI,
                    },
                )

                token_response.raise_for_status()

                token_data = token_response.json()

                access_token = token_data.get("access_token")

                if access_token is None:
                    raise OAuthProviderError("github")

                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                }

                user_response = await client.get(
                    settings.GITHUB_USER_URL,
                    headers=headers,
                )

                user_response.raise_for_status()

                emails_response = await client.get(
                    settings.GITHUB_EMAILS_URL,
                    headers=headers,
                )

                emails_response.raise_for_status()

                user_data = user_response.json()
                email_data = emails_response.json()

        except OAuthProviderError:
            raise

        except httpx.HTTPError:
            logger.exception("GitHub OAuth request failed")

            raise OAuthProviderError("github")

        email = self._get_primary_email(email_data)

        if email is None:
            raise OAuthEmailNotFoundError()

        return OAuthUserInfo(
            provider=OAuthProvider.GITHUB,

            provider_user_id=str(user_data["id"]),

            email=email,

            name=(
                user_data.get("name")
                or user_data.get("login")
                or email
            ),

            profile_image_url=user_data.get("avatar_url")
        )

    @staticmethod
    def _get_primary_email(emails: list[dict]) -> Optional[str]:

        primary = next(
            (
                item.get("email")
                for item in emails
                if item.get("primary")
            ),
            None,
        )

        if primary:
            return primary

        return next(
            (
                item.get("email")
                for item in emails
                if item.get("email")
            ),
            None,
        )