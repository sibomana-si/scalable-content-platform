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
    return Redis.from_url(settings.redis_url, decode_responses=True)
