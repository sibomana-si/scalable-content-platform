"""Shared test fixtures.

The API harness is an in-process httpx ``AsyncClient`` over ``ASGITransport`` (no live server).
Integration fixtures gate on **reachability** (is the TCP port open?), not on a successful query:
a local ``pytest`` with no compose stack skips integration, while CI, which provisions both
services, runs them and fails loudly if a reachable dependency is misconfigured
(rather than masking it as a skip).
"""

import itertools
import os
import socket
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from datetime import datetime
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.client import get_redis
from app.config import get_settings
from app.core.security import create_access_token
from app.db.session import get_engine, get_sessionmaker
from app.main import create_app
from app.models import Article, Role, User
from app.resilience.breaker import reset_breakers

# The chaos harness addresses the injector, not a dependency, so it has no Settings field.
TOXIPROXY_URL = os.environ.get("TOXIPROXY_URL", "http://localhost:8474")
TOXIPROXY_MYSQL_PORT = int(os.environ.get("TOXIPROXY_MYSQL_PORT", "23306"))
TOXIPROXY_REDIS_PORT = int(os.environ.get("TOXIPROXY_REDIS_PORT", "26379"))


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def db_available() -> None:
    """Skip unless the MySQL port is reachable (present-but-broken → let the test fail)."""

    settings = get_settings()
    if not _port_open(settings.mysql_host, settings.mysql_port):
        pytest.skip(f"MySQL not reachable at {settings.mysql_host}:{settings.mysql_port}")


@pytest.fixture
def redis_available() -> None:
    """Skip unless the Redis port is reachable (present-but-broken → let the test fail)."""

    url = urlparse(get_settings().redis_url)
    host, port = url.hostname or "localhost", url.port or 6379
    if not _port_open(host, port):
        pytest.skip(f"Redis not reachable at {host}:{port}")


@pytest.fixture
def chaos_available() -> None:
    """Skip unless the toxiproxy control port is reachable.

    CI runs a bare ``pytest`` with no marker selection, so a chaos test must decide for itself
    whether the harness is there. Gating on the port, not on a query, keeps a present-but-broken
    injector loud: the test runs and fails rather than skipping.
    """

    url = urlparse(TOXIPROXY_URL)
    host, port = url.hostname or "localhost", url.port or 8474
    if not _port_open(host, port):
        pytest.skip(f"toxiproxy not reachable at {host}:{port}")


# --- Database harness (integration/acceptance) ----------------------------------------------------
#
# The schema is built by running migrations. Isolation is delete-based cleanup rather than
# rollback-binding: acceptance tests exercise the app's real commit path, and the
# optimistic-concurrency tests need two independently committed sessions, both incompatible with
# binding every session to one rolled-back connection. ``TRUNCATE`` is not an option on FK parents.


@pytest.fixture(scope="session")
def migrated_db() -> None:
    """Skip unless MySQL is reachable, then bring the schema to ``head`` once per session."""

    settings = get_settings()
    if not _port_open(settings.mysql_host, settings.mysql_port):
        pytest.skip(f"MySQL not reachable at {settings.mysql_host}:{settings.mysql_port}")
    command.upgrade(Config("alembic.ini"), "head")


@pytest_asyncio.fixture
async def clean_db(migrated_db: None) -> AsyncGenerator[None, None]:
    """Empty ``articles``/``users`` around each test.

    The engine is disposed first so no pooled aiomysql connection created under a
    previous test's(now closed) event loop leaks into this one, and disposed again on
    teardown for the same reason in reverse.
    """

    async def _wipe() -> None:
        async with get_sessionmaker()() as session:
            await session.execute(text("DELETE FROM articles"))
            await session.execute(text("DELETE FROM users"))
            await session.commit()

    await get_engine().dispose()
    await _wipe()
    yield
    # Wipe on teardown too: leftover users rows would block the migration round-trip
    # test's role-seed downgrade (fk_users_role_id is ON DELETE RESTRICT).
    await _wipe()
    await get_engine().dispose()


# --- Cache harness -------------------------------------------------------------------------
#
# Redis outlives a test. Without a wipe, a cached article from one test answers the next test's
# read and the failure looks like a phantom — the row is gone from MySQL, yet the API returns it.
# Scoped to the app's own key namespaces rather than FLUSHDB, so a shared local Redis keeps
# whatever else is in it.

CACHE_KEY_PATTERNS = ("article:*", "articles:list:*", "lock:*")


async def wipe_cache() -> None:
    """Delete every key this application owns. Silent when Redis is unreachable.

    Opens its own client rather than the shared one. ``get_redis`` is ``lru_cache``d, so its
    pool belongs to whichever event loop first used it; a later test on a new loop would find
    that pool closed.
    """
    redis = Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
    try:
        for pattern in CACHE_KEY_PATTERNS:
            keys = [key async for key in redis.scan_iter(match=pattern, count=500)]
            if keys:
                await redis.delete(*keys)
    except (RedisError, TimeoutError, OSError):
        # A test that needs the cache gates on `redis_available`; the rest do not care.
        pass
    finally:
        await redis.aclose()


@pytest.fixture(autouse=True)
def _reset_breakers() -> Generator[None, None, None]:
    """Forget every circuit breaker around each test.

    A breaker lives for the life of the process, which is longer than a test. Without this, a
    test that drives a dependency down leaves the circuit open, and the next test measures the
    first one instead of itself.
    """
    reset_breakers()
    yield
    reset_breakers()


@pytest_asyncio.fixture(autouse=True)
async def _reset_redis_client() -> AsyncGenerator[None, None]:
    """Drop the shared Redis client after every test.

    ``get_redis`` is ``lru_cache``d, so its connection pool belongs to whichever event loop
    first used it. pytest-asyncio gives each test a new loop, so a client kept across tests
    raises "attached to a different loop" from inside the next test's request — a failure
    that names the middleware, not the cache. The same reasoning disposes the engine in
    ``clean_db``.
    """
    yield
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
        get_redis.cache_clear()


@pytest_asyncio.fixture
async def clean_cache() -> AsyncGenerator[None, None]:
    """Empty the application's cache keys around each test."""
    await wipe_cache()
    yield
    await wipe_cache()


@pytest_asyncio.fixture
async def db_session(clean_db: None) -> AsyncGenerator[AsyncSession, None]:
    """One AsyncSession on the app's sessionmaker, against the migrated, clean schema."""

    async with get_sessionmaker()() as session:
        yield session


@pytest_asyncio.fixture
async def user_factory(db_session: AsyncSession) -> Callable[..., Awaitable[User]]:
    """Create committed users; role is looked up by name."""

    counter = itertools.count(1)

    async def _create(
        email: str | None = None,
        role: str = "user",
        password_hash: str = "$argon2id$test-not-a-real-hash",
    ) -> User:
        role_row = (await db_session.execute(select(Role).where(Role.name == role))).scalar_one()
        user = User(
            email=email or f"user{next(counter)}@example.com",
            password_hash=password_hash,
            role=role_row,  # assign the relationship so user.role.name is loaded for token minting
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    return _create


@pytest_asyncio.fixture
async def article_factory(
    db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
) -> Callable[..., Awaitable[Article]]:
    """Create committed articles; explicit ``created_at`` (for keyset tie/ordering tests)
    and ``deleted_at`` (soft-deleted state) are settable."""

    counter = itertools.count(1)

    async def _create(
        author: User | None = None,
        title: str | None = None,
        body: str = "Body of the article",
        created_at: datetime | None = None,
        deleted_at: datetime | None = None,
    ) -> Article:
        if author is None:
            author = await user_factory()
        article = Article(
            author_id=author.id,
            title=title or f"Article {next(counter)}",
            body=body,
            deleted_at=deleted_at,
        )
        if created_at is not None:
            article.created_at = created_at
            article.updated_at = created_at
        db_session.add(article)
        await db_session.commit()
        await db_session.refresh(article)
        return article

    return _create


@pytest.fixture
def auth_headers() -> Callable[[User], dict[str, str]]:
    """Mint a real Bearer JWT for the user, matching the production auth path."""

    def _headers(user: User) -> dict[str, str]:
        token = create_access_token(sub=str(user.id), role=user.role.name)
        return {"Authorization": f"Bearer {token}"}

    return _headers
