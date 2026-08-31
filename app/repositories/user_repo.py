from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Role, User
from app.resilience.guard import guarded_read, guarded_write


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @guarded_read("mysql")
    async def get_by_id(self, user_id: int) -> User | None:
        # The role relationship is joined-eager on the model, so the role name needed
        # for authorization arrives in the same SELECT.
        return await self._session.get(User, user_id)

    @guarded_read("mysql")
    async def get_by_email(self, email: str) -> User | None:
        # Joined-eager role travels with the row (needed to embed role in the login token).
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    @guarded_write("mysql")
    async def create(self, *, email: str, password_hash: str, role_name: str) -> User:
        role = await self._get_role(role_name)
        # Assigning the relationship (not just role_id) keeps role loaded on the returned
        # instance so callers can read role.name without another query.
        user = User(email=email, password_hash=password_hash, role=role)
        self._session.add(user)
        # Flush (not commit): the INSERT runs and the PK is assigned within the request
        # transaction owned by get_session; the handler still never commits.
        await self._session.flush()
        return user

    # Not guarded: it runs inside `create`, which already holds the guard. A nested guard would
    # spend the breaker twice for one logical write.
    async def _get_role(self, role_name: str) -> Role:
        result = await self._session.execute(select(Role).where(Role.name == role_name))
        return result.scalar_one()
