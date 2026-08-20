"""Async SQLAlchemy engine, session factory, and request-scoped session dependency.

The engine and sessionmaker are created lazily and cached, so importing this module never
opens a connection; the first real connection happens on first use (keeps ``/health/live``
dependency-free). The async app driver is aiomysql (``mysql+aiomysql://``).
"""

from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Any

from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings
from app.db.after_commit import discard_after_commit, drain_after_commit
from app.observability.metrics import attach_query_metrics, register_pool_metrics


def build_engine_kwargs(settings: Settings) -> dict[str, Any]:
    """The pool settings, validated. Separate from engine creation so it is unit-testable.

    Each bound makes a failure bounded. Rejecting the unbounded forms outright — a zero
    ``pool_timeout``, a negative ``max_overflow`` — matters because SQLAlchemy reads both as
    "no limit", so a typo in an env var silently removes the ceiling instead of failing.
    """
    if settings.db_pool_timeout <= 0:
        raise ValueError(
            "DB_POOL_TIMEOUT must be positive; a non-positive value waits for a connection "
            "forever, so a saturated pool stalls instead of failing."
        )
    if settings.db_max_overflow < 0:
        raise ValueError(
            "DB_MAX_OVERFLOW must not be negative; SQLAlchemy reads a negative value as "
            "unlimited overflow, which removes the ceiling the capacity model assumes."
        )
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
        # A connection the server closed underneath the pool must fail on checkout, where a
        # retry is cheap, not mid-statement where it is a 500.
        "pool_pre_ping": True,
    }


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    engine = create_async_engine(settings.async_database_url, **build_engine_kwargs(settings))
    # DBAPI-level cursor events live on the sync engine underneath the async facade.
    attach_query_metrics(engine.sync_engine)
    return engine


# Exports db_pool_connections{state}. Registered at import, outside get_engine: the
# collector holds get_engine as a callable and resolves it at scrape time, so nothing
# connects here — and registering from inside get_engine would re-enter an lru_cache'd
# function that had not returned yet.
register_pool_metrics(REGISTRY, get_engine)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: one AsyncSession, one transaction per request.

    The 'begin()' block commits when the handler returns cleanly and rolls back when
    anything raises through it (domain errors included; they become responses in the
    app-level exception handlers after this teardown). Handlers never commit.

    Because the commit is this generator's teardown, callers must declare the dependency
    with ``scope="function"``. FastAPI's default for a dependency with yield is
    ``scope="request"``, which runs teardown after the response has been sent to the
    client — the write would then be announced before it was durable.

    After the commit, the after-commit queue drains. Cache invalidation registers there
    rather than running inline, because an inline ``DEL`` would run before the row was
    durable and a concurrent reader could repopulate the cache with the pre-commit value.
    A rollback discards the queue instead: no commit, no new value, nothing to invalidate to.
    """

    async with get_sessionmaker()() as session:
        try:
            async with session.begin():
                yield session
        except BaseException:
            discard_after_commit(session)
            raise
        await drain_after_commit(session)
