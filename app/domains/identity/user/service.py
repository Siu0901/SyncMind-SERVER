from app.domains.identity.user.model import User
from app.domains.identity.user.repository import UserRepository


class UserQueryService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.repository.get_by_id(user_id)
