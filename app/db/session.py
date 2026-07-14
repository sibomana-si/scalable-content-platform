"""Async SQLAlchemy engine, session factory, and request-scoped session dependency.

The engine and sessionmaker are created lazily and cached, so importing this module never
opens a connection - the first real connection happens on first use (keeps ``/health/live``
dependency-free). The async app driver is aiomysql (``mysql+aiomysql://``).
"""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.async_database_url,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding one AsyncSession per request."""

    async with get_sessionmaker()() as session:
        yield session
