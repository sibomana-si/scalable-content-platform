"""Async Redis client accessor (redis.asyncio).

Cached so the whole process shares one connection pool; constructing it opens no socket
until the first command (keeps ``/health/live`` dependency-free).
"""

from functools import lru_cache
from typing import Any

from redis.asyncio import Redis

from app.config import Settings, get_settings


def build_redis_kwargs(settings: Settings) -> dict[str, Any]:
    """The client settings, validated. Separate from client creation so it is unit-testable."""

    if settings.redis_max_connections <= 0:
        raise ValueError(
            "REDIS_MAX_CONNECTIONS must be positive; redis-py reads a non-positive value as "
            "an unbounded pool, so a stalled server opens a socket per waiting caller."
        )
    return {
        "decode_responses": True,
        # Bounded on purpose: the library default is None, which turns a blackholed Redis into
        # an indefinite wait for every caller. A timed-out command raises redis.TimeoutError,
        # which callers can degrade on — an unbounded one gives them nothing to react to.
        "socket_timeout": settings.redis_socket_timeout,
        "socket_connect_timeout": settings.redis_socket_connect_timeout,
        # The pool ceiling. Without it a stalled Redis converts every waiting request into a
        # new socket, and the cache outage becomes a file-descriptor outage.
        "max_connections": settings.redis_max_connections,
    }


@lru_cache
def get_redis() -> Redis:
    """FastAPI dependency returning the shared async Redis client."""

    return Redis.from_url(get_settings().redis_url, **build_redis_kwargs(get_settings()))
