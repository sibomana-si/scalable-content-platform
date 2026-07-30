"""Inner-loop unit tests for AuthService: registration and login rules, no I/O.

The repository is an in-memory fake (the real one is covered by integration/acceptance
tests against MySQL). An autouse fixture pins a signing key so login can mint tokens.
"""

import itertools

import pytest

from app.config import get_settings
from app.core.security import decode_access_token
from app.models import Role, User
from app.services.auth_service import AuthService
from app.services.exceptions import ConflictError, UnauthenticatedError


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    monkeypatch.setenv("JWT_SECRET", "unit-test-signing-key")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeUserRepository:
    """In-memory stand-in mirroring UserRepository's interface."""

    def __init__(self) -> None:
        self.rows: dict[int, User] = {}
        self._ids = itertools.count(1)
        self._roles = {"user": 1, "admin": 2}

    async def get_by_email(self, email: str) -> User | None:
        for user in self.rows.values():
            if user.email == email:
                return user
        return None

    async def create(self, *, email: str, password_hash: str, role_name: str) -> User:
        role_id = self._roles[role_name]
        user = User(
            id=next(self._ids),
            email=email,
            password_hash=password_hash,
            role_id=role_id,
            role=Role(id=role_id, name=role_name),
        )
        self.rows[user.id] = user
        return user


@pytest.fixture
def repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def service(repo: FakeUserRepository) -> AuthService:
    return AuthService(repo)  # type: ignore[arg-type]


# --- register -----------------------------------------------------------------------------


async def test_register_hashes_password_and_assigns_default_role(service, repo):
    user = await service.register(email="a@example.com", password="a-strong-password")

    assert user.email == "a@example.com"
    assert user.role.name == "user"
    stored = repo.rows[user.id]
    assert stored.password_hash != "a-strong-password"
    assert stored.password_hash.startswith("$argon2")


async def test_register_duplicate_email_raises_conflict(service):
    await service.register(email="dup@example.com", password="a-strong-password")
    with pytest.raises(ConflictError):
        await service.register(email="dup@example.com", password="another-password")


# --- login --------------------------------------------------------------------------------


async def test_login_returns_token_for_valid_credentials(service):
    await service.register(email="live@example.com", password="a-strong-password")

    token = await service.login(email="live@example.com", password="a-strong-password")

    claims = decode_access_token(token)
    assert claims["role"] == "user"
    assert claims["sub"]


async def test_login_unknown_email_raises_generic_unauthenticated(service):
    with pytest.raises(UnauthenticatedError):
        await service.login(email="nobody@example.com", password="whatever-password")


async def test_login_wrong_password_raises_generic_unauthenticated(service):
    await service.register(email="live@example.com", password="a-strong-password")
    with pytest.raises(UnauthenticatedError):
        await service.login(email="live@example.com", password="wrong-password")


async def test_login_no_enumeration_same_message_both_ways(service, repo):
    # Register one user; the failure message must be identical for unknown-email and
    # wrong-password so a caller cannot distinguish the two (no user enumeration).
    await service.register(email="live@example.com", password="a-strong-password")

    with pytest.raises(UnauthenticatedError) as unknown:
        await service.login(email="ghost@example.com", password="a-strong-password")
    with pytest.raises(UnauthenticatedError) as wrong:
        await service.login(email="live@example.com", password="bad-password")

    assert unknown.value.message == wrong.value.message
