from app.domains.identity.auth.exceptions import OAuthProviderError
from app.domains.identity.auth.enums import OAuthProvider
from app.domains.identity.auth.oauth.port import OAuthClient
from app.domains.identity.auth.oauth.adapter.github import GitHubOAuthClient
from app.domains.identity.auth.oauth.adapter.google import GoogleOAuthClient


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
