from abc import ABC, abstractmethod

from app.domains.auth.schema import OAuthUserInfo


class OAuthClient(ABC):

    @abstractmethod
    def create_authorization_url(self, state: str) -> str:
        pass

    @abstractmethod
    async def get_user_info(self, code: str) -> OAuthUserInfo:
        pass