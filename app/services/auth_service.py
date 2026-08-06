"""Authentication domain rules: registration and login."""

from functools import lru_cache

from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.observability.tracing import traced
from app.repositories.user_repo import UserRepository
from app.services.exceptions import ConflictError, UnauthenticatedError


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A throwaway Argon2 hash used to equalize login timing for unknown emails, so an
    absent account is not distinguishable from a wrong password by response time."""

    return hash_password("timing-equalizer-not-a-real-password")


class AuthService:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def register(self, *, email: str, password: str) -> User:
        # Span attributes deliberately carry no email/password: a trace backend is not a
        # place for credentials or PII.
        async with traced("auth", "register"):
            if await self._users.get_by_email(email) is not None:
                raise ConflictError("A user with this email already exists.")
            return await self._users.create(
                email=email,
                password_hash=hash_password(password),
                role_name="user",  # registration always yields the least-privileged role
            )

    async def login(self, *, email: str, password: str) -> str:
        async with traced("auth", "login"):
            user = await self._users.get_by_email(email)
            if user is None:
                # Spend comparable time so unknown-email and wrong-password are indistinguishable.
                verify_password(password, _dummy_hash())
                raise UnauthenticatedError("Invalid email or password.")
            if not verify_password(password, user.password_hash):
                raise UnauthenticatedError("Invalid email or password.")
            return create_access_token(sub=str(user.id), role=user.role.name)
