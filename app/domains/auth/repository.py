from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domains.auth.enums import OAuthProvider
from app.domains.auth.model import OAuthAccount


class OAuthAccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_provider_user(
        self,
        provider: OAuthProvider,
        provider_user_id: str,
    ) -> Optional[OAuthAccount]:
        statement = select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_user_id== provider_user_id,
        )

        result = await self.session.exec(statement)

        return result.first()

    async def create(self, account: OAuthAccount) -> OAuthAccount:
        self.session.add(account)
        await self.session.refresh(account)
        await self.session.commit()

        return account