from app.domains.auth.exceptions import OAuthProviderError
from app.domains.auth.enums import OAuthProvider
from app.domains.auth.oauth.base import OAuthClient
from app.domains.auth.oauth.github import GitHubOAuthClient
from app.domains.auth.oauth.google import GoogleOAuthClient


class OAuthClientFactory:
    def __init__(
        self,
        google: GoogleOAuthClient,
        github: GitHubOAuthClient,
    ):
        self.google = google
        self.github = github

    def get(self, provider: OAuthProvider) -> OAuthClient:

        match provider:

            case OAuthProvider.GOOGLE:
                return self.google

            case OAuthProvider.GITHUB:
                return self.github

        raise OAuthProviderError(
            provider.value
        )