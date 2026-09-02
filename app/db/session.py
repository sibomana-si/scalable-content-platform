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
    if settings.db_connect_timeout_seconds <= 0:
        raise ValueError(
            "DB_CONNECT_TIMEOUT_SECONDS must be positive; aiomysql reads a non-positive value "
            "as no timeout, so a blackholed host holds the request until the kernel gives up."
        )
    if settings.db_statement_timeout_seconds <= 0:
        raise ValueError(
            "DB_STATEMENT_TIMEOUT_SECONDS must be positive; MySQL reads max_execution_time=0 "
            "as no limit, so a runaway query keeps its connection until it finishes."
        )
    # MySQL takes this in milliseconds. Passing seconds would make a 2 s bound a 2 ms bound,
    # and every SELECT would fail under a name that reads like a success.
    statement_timeout_ms = int(settings.db_statement_timeout_seconds * 1000)
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
        # A connection the server closed underneath the pool must fail on checkout, where a
        # retry is cheap, not mid-statement where it is a 500.
        "pool_pre_ping": True,
        "connect_args": {
            "connect_timeout": settings.db_connect_timeout_seconds,
            # The server-side half of the statement bound. A client that gives up alone leaves
            # MySQL running the query and the connection pinned to it, so the pool loses the
            # connection for the length of the query rather than the length of the timeout.
            # `max_execution_time` covers read-only SELECT statements; writes are bounded by
            # the client timeout and by `innodb_lock_wait_timeout`.
            "init_command": f"SET SESSION max_execution_time={statement_timeout_ms}",
        },
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

    The commit runs when the handler returns cleanly, and the rollback runs when anything
    raises through it (domain errors included; they become responses in the app-level
    exception handlers after this teardown). Handlers never commit.

    Commit and rollback are explicit rather than an ``async with session.begin()`` block.
    That block refuses every statement issued after a rollback inside it, and ``with_retry``
    rolls the session back between attempts. So a read whose connection died mid-transaction
    retried into ``InvalidRequestError``, which the retry layer does not call transient:
    nothing translated it, the guard read it as an answer from a working dependency,
    and the API answered 500 while the breaker stayed closed.
    With the explicit form the session autobegins again, so the second attempt reaches
    MySQL and a real refusal answers 503. Only reads retry, so no write ever crosses
    two transactions.

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
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            discard_after_commit(session)
            raise
        await drain_after_commit(session)
