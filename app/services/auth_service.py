"""Authentication domain rules: registration and login."""

from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.observability.tracing import traced
from app.repositories.user_repo import UserRepository
from app.services.exceptions import ConflictError, UnauthenticatedError

# Memoized by hand rather than with lru_cache: hashing is a coroutine now. Two concurrent
# first calls would each compute one, which is harmless — the value is a throwaway.
_dummy_hash_cache: str | None = None


async def _dummy_hash() -> str:
    """A throwaway Argon2 hash used to equalize login timing for unknown emails, so an
    absent account is not distinguishable from a wrong password by response time."""
    global _dummy_hash_cache
    if _dummy_hash_cache is None:
        _dummy_hash_cache = await hash_password("timing-equalizer-not-a-real-password")
    return _dummy_hash_cache


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
                password_hash=await hash_password(password),
                role_name="user",  # registration always yields the least-privileged role
            )

    async def login(self, *, email: str, password: str) -> str:
        async with traced("auth", "login"):
            user = await self._users.get_by_email(email)
            if user is None:
                # Spend comparable time so unknown-email and wrong-password are indistinguishable.
                await verify_password(password, await _dummy_hash())
                raise UnauthenticatedError("Invalid email or password.")
            if not await verify_password(password, user.password_hash):
                raise UnauthenticatedError("Invalid email or password.")
            return create_access_token(sub=str(user.id), role=user.role.name)
