"""Async Redis client accessor (redis.asyncio).

Cached so the whole process shares one connection pool; constructing it opens no socket
until the first command (keeps ``/health/live`` dependency-free).
"""

from functools import lru_cache

from redis.asyncio import Redis

from app.config import get_settings


@lru_cache
def get_redis() -> Redis:
    """FastAPI dependency returning the shared async Redis client."""

    settings = get_settings()
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        # Bounded on purpose: the library default is None, which turns a blackholed Redis into
        # an indefinite wait for every caller. A timed-out command raises redis.TimeoutError,
        # which callers can degrade on — an unbounded one gives them nothing to react to.
        socket_timeout=settings.redis_socket_timeout,
        socket_connect_timeout=settings.redis_socket_connect_timeout,
    )
