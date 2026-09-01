"""The Redis-facing half of the article cache-aside path.

The cache stores response bodies as JSON strings. It never stores ORM objects, and it never
decides anything: authorization and compare-and-set read MySQL directly.
"""

from typing import Any

import structlog
from redis.exceptions import RedisError

from app.cache.keys import (
    ARTICLE_TTL_ENTITY,
    LIST_TTL_ENTITY,
    article_key,
    author_generation_key,
    generation_key,
    lock_key,
)
from app.observability.metrics import (
    METRICS,
    Metrics,
    observe_cache_error,
    observe_cache_hit,
    observe_cache_miss,
)
from app.resilience.breaker import CircuitBreaker, get_breaker

log = structlog.get_logger(__name__)

# Redis raises TimeoutError from the standard library for a socket timeout, and RedisError for
# everything else. Both mean the same thing here: the cache did not answer, so read MySQL.
CACHE_FAILURES = (RedisError, TimeoutError, OSError)


class ArticleCache:
    def __init__(
        self,
        redis: Any,
        *,
        metrics: Metrics = METRICS,
        enabled: bool = True,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._redis = redis
        self._metrics = metrics
        self._enabled = enabled
        self._degraded = False
        # Resolved on first use, never at construction: a disabled cache must read no settings
        # and build nothing. `_NO_CACHE` in the service layer is built at import time.
        self._breaker = breaker

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def degraded(self) -> bool:
        """True once a command has failed on this instance."""
        return self._degraded

    @property
    def breaker(self) -> CircuitBreaker | None:
        """The shared Redis breaker, or ``None`` while the cache is disabled or untouched."""
        return self._breaker

    def _live(self) -> bool:
        """Whether it is still worth sending a command.

        Three gates, cheapest first. The configuration switch never changes. The per-instance
        latch stops one request from paying the socket timeout four times over. The breaker
        stops the next thousand requests from each paying it once — the latch cannot, because
        an instance lives for one request and forgets everything at the end of it.
        """
        if not self._enabled or self._degraded:
            return False
        if self._breaker is None:
            self._breaker = get_breaker("redis")
        return self._breaker.allow()

    def _succeed(self) -> None:
        """Redis answered. Closes the circuit again when this call was the probe."""
        if self._breaker is not None:
            self._breaker.record_success()

    def _degrade(self, operation: str, error: Exception) -> None:
        """Count and log one failed cache operation, then stop using this instance.

        The latch is the point. A cache-aside read makes four Redis calls — get, lock, set,
        release — and a blackholed server answers none of them, so each pays the full socket
        timeout and a 2-second timeout becomes an 8-second request. The read still succeeds,
        which makes it worse: nothing fails and everything is slow. One failure is enough
        evidence to skip the rest.

        An instance lives for one request (``get_article_service`` builds it per request), so
        the latch closes for that request only and the next one probes Redis again.
        """
        self._degraded = True
        if self._breaker is not None:
            # One failure per instance, so the count that opens the circuit counts requests,
            # not commands. That is the right unit: the question is whether Redis is down.
            self._breaker.record_failure()
        observe_cache_error(operation, metrics=self._metrics)
        log.warning("cache.degraded", operation=operation, error=str(error))

    # --- reads ----------------------------------------------------------------------------

    async def get_article(self, article_id: int) -> str | None:
        """The cached article body, or ``None`` on a miss, a disabled cache, or a failure."""
        return await self._get(article_key(article_id), ARTICLE_TTL_ENTITY)

    async def get_list_page(self, key: str) -> str | None:
        """The cached list page body for an already-built key."""
        return await self._get(key, LIST_TTL_ENTITY)

    async def peek(self, key: str) -> str | None:
        """Read a key without counting a hit or a miss.

        The single-flight loser polls this while it waits. Counting each poll would inflate
        the hit ratio with reads that are an implementation detail of one logical miss.
        """
        if not self._live():
            return None
        try:
            value = await self._redis.get(key)
        except CACHE_FAILURES as error:
            self._degrade("get", error)
            return None
        self._succeed()
        return value

    async def _get(self, key: str, entity: str) -> str | None:
        if not self._live():
            # A bypassed cache has no hit ratio. Counting a miss here would make a deliberate
            # configuration read as a fault on the dashboard.
            return None
        try:
            value = await self._redis.get(key)
        except CACHE_FAILURES as error:
            self._degrade("get", error)
            return None
        self._succeed()
        if value is None:
            observe_cache_miss(entity, metrics=self._metrics)
            return None
        observe_cache_hit(entity, metrics=self._metrics)
        return value

    async def get_generations(self, *, author_id: int | None) -> tuple[int, int]:
        """The global and per-author list generations, in one round trip.

        An unset, corrupt, or unreachable counter reads as ``0``. Every failure direction is
        safe: the reader builds a key nobody has written, misses, and loads from MySQL.
        """
        if not self._live():
            return 0, 0
        keys = [generation_key()]
        if author_id is not None:
            keys.append(author_generation_key(author_id))
        try:
            values = await self._redis.mget(keys)
        except CACHE_FAILURES as error:
            self._degrade("get", error)
            return 0, 0
        self._succeed()
        parsed = [_as_int(value) for value in values]
        parsed += [0] * (2 - len(parsed))
        return parsed[0], parsed[1]

    # --- writes ---------------------------------------------------------------------------

    async def set_article(self, article_id: int, body: str, *, ttl_seconds: int) -> None:
        await self._set(article_key(article_id), body, ttl_seconds)

    async def set_list_page(self, key: str, body: str, *, ttl_seconds: int) -> None:
        await self._set(key, body, ttl_seconds)

    async def _set(self, key: str, body: str, ttl_seconds: int) -> None:
        if not self._live():
            return
        try:
            # Always with an expiry: nothing else in this design removes a key, so a write
            # without a TTL is a permanent leak.
            await self._redis.set(key, body, ex=max(1, ttl_seconds))
        except CACHE_FAILURES as error:
            self._degrade("set", error)
            return
        self._succeed()

    async def invalidate_article(self, article_id: int) -> None:
        """Drop one cached article body. Called after the write commits."""
        if not self._live():
            return
        try:
            await self._redis.delete(article_key(article_id))
        except CACHE_FAILURES as error:
            self._degrade("delete", error)
            return
        self._succeed()

    async def bump_generations(self, *, author_id: int | None) -> None:
        """Move the counters a write can affect, making every page under them unreachable.

        O(1) whatever the cache holds. A write by one author leaves the other authors'
        pages addressable, which is the whole point of the second counter.
        """
        if not self._live():
            return
        try:
            await self._redis.incr(generation_key())
            if author_id is not None:
                await self._redis.incr(author_generation_key(author_id))
        except CACHE_FAILURES as error:
            self._degrade("incr", error)
            return
        self._succeed()

    # --- single flight ----------------------------------------------------------------------

    async def acquire_lock(self, key: str, *, ttl_seconds: int) -> bool:
        """Claim the right to load ``key`` from the database.

        Returns ``True`` for the winner, and also whenever the lock machinery itself is
        unavailable: losing stampede control costs duplicate queries, and that is strictly
        better than a request that cannot proceed.
        """
        if not self._live():
            return True
        try:
            acquired = await self._redis.set(lock_key(key), "1", ex=max(1, ttl_seconds), nx=True)
        except CACHE_FAILURES as error:
            self._degrade("lock", error)
            return True
        self._succeed()
        return bool(acquired)

    async def release_lock(self, key: str) -> None:
        if not self._live():
            return
        try:
            await self._redis.delete(lock_key(key))
        except CACHE_FAILURES as error:
            self._degrade("lock", error)
            return
        self._succeed()


def _as_int(value: Any) -> int:
    """A counter value, or ``0`` for anything that is not one."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
