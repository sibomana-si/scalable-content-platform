from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: int) -> User | None:
        # The role relationship is joined-eager on the model, so the role name needed
        # for authorization arrives in the same SELECT.
        return await self._session.get(User, user_id)
