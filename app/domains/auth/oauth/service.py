import logging
import secrets
from urllib.parse import urlencode

from arq.connections import ArqRedis
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import get_settings
from app.domains.auth.exceptions import (
    InactiveUserError,
    InvalidOAuthStateError,
    OAuthEmailConflictError,
)
from app.core.security import AuthManager
from app.domains.auth.enums import OAuthProvider
from app.domains.auth.model import OAuthAccount
from app.domains.auth.oauth.factory import OAuthClientFactory
from app.domains.auth.repository import OAuthAccountRepository
from app.domains.auth.schema import OAuthUserInfo
from app.domains.user.model import User
from app.domains.user.repository import UserRepository


settings = get_settings()

logger = logging.getLogger(__name__)


class OAuthService:
    STATE_PREFIX = "auth:oauth:state"
    TICKET_PREFIX = "auth:oauth:ticket"

    def __init__(
        self,
        session: AsyncSession,
        redis: ArqRedis,
        auth_manager: AuthManager,
        users_repo: UserRepository,
        oauth_accounts: OAuthAccountRepository,
        oauth_factory: OAuthClientFactory,
    ):
        self.session = session
        self.redis = redis
        self.auth_manager = auth_manager
        self.users_repo = users_repo
        self.oauth_accounts = oauth_accounts
        self.oauth_factory = oauth_factory


    async def create_login_url(self, provider: OAuthProvider) -> str:

        state = secrets.token_urlsafe(32)

        key = f"{self.STATE_PREFIX}:{state}"

        await self.redis.setex(
            key,
            600,
            provider.value,
        )

        client = self.oauth_factory.get(provider)

        logger.info(
            "OAuth login started | provider=%s",
            provider.value,
        )

        return client.create_authorization_url(state)


    async def handle_callback(
        self,
        provider: OAuthProvider,
        code: str,
        state: str,
    ) -> str:

        await self._validate_state(provider, state)

        client = self.oauth_factory.get(provider)

        user_info = await client.get_user_info(code)

        user = await self._resolve_user(user_info)

        ticket = await self._create_ticket(user.id)

        logger.info(
            "OAuth login succeeded | provider=%s user_id=%s",
            provider.value,
            user.id,
        )

        query = urlencode({"ticket": ticket})

        return f"{settings.FRONTEND_OAUTH_CALLBACK_URL}?{query}"


    async def _validate_state(
        self,
        provider: OAuthProvider,
        state: str,
    ):

        key = f"{self.STATE_PREFIX}:{state}"

        stored_provider = await self.redis.get(key)

        if stored_provider is None:
            raise InvalidOAuthStateError()

        await self.redis.delete(key)

        if isinstance(stored_provider, bytes):
            stored_provider = stored_provider.decode()

        if stored_provider != provider.value:
            raise InvalidOAuthStateError()


    async def _resolve_user(self, info: OAuthUserInfo) -> User:

        oauth_account = (
            await self.oauth_accounts
            .get_by_provider_identity(
                info.provider,
                info.provider_user_id,
            )
        )

        if oauth_account is not None:
            user = (
                await self.users_repo.get_by_id(
                    oauth_account.user_id
                )
            )

            if user is None:
                raise OAuthEmailConflictError()

            if not user.is_active:
                raise InactiveUserError()

            return user

        email = self.auth_manager.normalize_email(info.email)

        existing_user = await self.users_repo.get_by_email(email)

        if existing_user is not None:
            raise OAuthEmailConflictError()

        user = User(
            email=email,
            password_hash=None,
            name=info.name,
            profile_image_url=info.profile_image_url,
            email_verified=True,
            is_active=True,
        )

        await self.users_repo.create(user)

        oauth_account = OAuthAccount(
            user_id=user.id,
            provider=info.provider,
            provider_user_id=info.provider_user_id,
            provider_email=email,
        )

        await self.oauth_accounts.create(oauth_account)

        try:
            await self.session.commit()

        except Exception:
            await self.session.rollback()
            raise

        return user


    async def _create_ticket(self, user_id: int) -> str:

        ticket = secrets.token_urlsafe(32)

        key = f"{self.TICKET_PREFIX}:{ticket}"

        await self.redis.setex(
            key,
            60,
            str(user_id),
        )

        return ticket