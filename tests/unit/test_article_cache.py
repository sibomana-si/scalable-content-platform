"""Unit tests for ArticleCache: the only module that talks to Redis on the read path.

Two fakes stand in for Redis — an in-memory one that behaves, and one that raises on every
command. The second is the important one. ADR-0004 makes the cache an optimization and never
a dependency, so no method here may raise into a request. Every failure must return
``None`` (read) or do nothing (write), and count itself in ``cache_errors_total``.
"""

import pytest
from prometheus_client import CollectorRegistry
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from app.cache.article_cache import ArticleCache
from app.cache.keys import article_key, author_generation_key, generation_key
from app.observability.metrics import Metrics, build_metrics


class FakeRedis:
    """Minimal in-memory stand-in for the redis.asyncio commands ArticleCache uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.calls: list[tuple] = []

    async def get(self, key):
        self.calls.append(("get", key))
        return self.store.get(key)

    async def mget(self, keys):
        self.calls.append(("mget", tuple(keys)))
        return [self.store.get(key) for key in keys]

    async def set(self, key, value, ex=None, nx=False):
        self.calls.append(("set", key, ex, nx))
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def delete(self, *keys):
        self.calls.append(("delete", *keys))
        return sum(self.store.pop(key, None) is not None for key in keys)

    async def incr(self, key):
        self.calls.append(("incr", key))
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = str(value)
        return value


class RaisingRedis(FakeRedis):
    """Every command fails. Stands in for Redis down, blackholed, or overloaded."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self._error = error or RedisConnectionError("connection refused")

    async def get(self, key):
        raise self._error

    async def mget(self, keys):
        raise self._error

    async def set(self, key, value, ex=None, nx=False):
        raise self._error

    async def delete(self, *keys):
        raise self._error

    async def incr(self, key):
        raise self._error


@pytest.fixture
def metrics() -> Metrics:
    return build_metrics(CollectorRegistry())


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def cache(redis: FakeRedis, metrics: Metrics) -> ArticleCache:
    return ArticleCache(redis, metrics=metrics)


def counter(m: Metrics, name: str, labels: dict[str, str]) -> float:
    return m.registry.get_sample_value(name, labels) or 0.0


BODY = '{"id": 42, "title": "t"}'


# --- reads ------------------------------------------------------------------------------------


async def test_get_returns_none_on_a_miss(cache, metrics):
    assert await cache.get_article(42) is None
    assert counter(metrics, "cache_misses_total", {"entity": "article"}) == 1.0


async def test_get_returns_the_stored_body_on_a_hit(cache, redis, metrics):
    redis.store[article_key(42)] = BODY

    assert await cache.get_article(42) == BODY
    assert counter(metrics, "cache_hits_total", {"entity": "article"}) == 1.0
    assert counter(metrics, "cache_misses_total", {"entity": "article"}) == 0.0


async def test_set_article_stores_the_body_under_a_bounded_ttl(cache, redis):
    await cache.set_article(42, BODY, ttl_seconds=300)

    assert redis.store[article_key(42)] == BODY
    assert redis.ttls[article_key(42)] == 300


async def test_set_article_never_writes_without_an_expiry(cache, redis):
    """A key with no TTL is a leak: nothing else in the design ever removes it."""

    await cache.set_article(42, BODY, ttl_seconds=300)
    expiries = [call[2] for call in redis.calls if call[0] == "set"]

    assert all(ex is not None and ex > 0 for ex in expiries)


async def test_list_page_round_trips(cache, metrics):
    await cache.set_list_page("articles:list:g1:all:abc", BODY, ttl_seconds=60)

    assert await cache.get_list_page("articles:list:g1:all:abc") == BODY
    assert counter(metrics, "cache_hits_total", {"entity": "list"}) == 1.0


async def test_list_miss_counts_under_the_list_entity(cache, metrics):
    assert await cache.get_list_page("articles:list:g1:all:abc") is None
    assert counter(metrics, "cache_misses_total", {"entity": "list"}) == 1.0


# --- generations ---------------------------------------------------------------------------


async def test_generations_start_at_zero_when_unset(cache):
    assert await cache.get_generations(author_id=None) == (0, 0)


async def test_generations_read_both_counters_in_one_round_trip(cache, redis):
    """Two sequential GETs would double the latency the cache exists to remove."""

    redis.store[generation_key()] = "7"
    redis.store[author_generation_key(5)] = "3"

    assert await cache.get_generations(author_id=5) == (7, 3)
    assert [call for call in redis.calls if call[0] == "get"] == []
    assert ("mget", (generation_key(), author_generation_key(5))) in redis.calls


async def test_a_corrupt_generation_value_reads_as_zero(cache, redis):
    """A non-integer counter must degrade to a cold cache, never raise into a request."""

    redis.store[generation_key()] = "not-a-number"

    assert await cache.get_generations(author_id=None) == (0, 0)


# --- writes -------------------------------------------------------------------------------


async def test_invalidate_article_deletes_the_detail_key(cache, redis):
    redis.store[article_key(42)] = BODY
    await cache.invalidate_article(42)

    assert article_key(42) not in redis.store


async def test_bump_generations_moves_both_counters(cache, redis):
    await cache.bump_generations(author_id=5)

    assert redis.store[generation_key()] == "1"
    assert redis.store[author_generation_key(5)] == "1"


async def test_bump_leaves_another_authors_generation_untouched(cache, redis):
    """The property the per-author counter buys: one author's write spares the others."""

    redis.store[author_generation_key(9)] = "4"
    await cache.bump_generations(author_id=5)

    assert redis.store[author_generation_key(9)] == "4"


# --- single flight ---------------------------------------------------------------------------


async def test_acquire_lock_succeeds_for_the_first_caller(cache):
    assert await cache.acquire_lock("article:42", ttl_seconds=2) is True


async def test_acquire_lock_fails_for_the_second_caller(cache):
    await cache.acquire_lock("article:42", ttl_seconds=2)

    assert await cache.acquire_lock("article:42", ttl_seconds=2) is False


async def test_the_lock_carries_an_expiry_so_a_dead_loader_cannot_hold_it(cache, redis):
    await cache.acquire_lock("article:42", ttl_seconds=2)
    lock_sets = [call for call in redis.calls if call[0] == "set" and call[3] is True]

    assert lock_sets and all(call[2] == 2 for call in lock_sets)


async def test_release_lock_lets_the_next_caller_through(cache):
    await cache.acquire_lock("article:42", ttl_seconds=2)
    await cache.release_lock("article:42")

    assert await cache.acquire_lock("article:42", ttl_seconds=2) is True


# --- fail-open ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error", [RedisConnectionError("down"), RedisError("boom"), TimeoutError()]
)
async def test_a_read_error_degrades_to_a_miss(error, metrics):
    cache = ArticleCache(RaisingRedis(error), metrics=metrics)

    assert await cache.get_article(42) is None
    assert counter(metrics, "cache_errors_total", {"operation": "get"}) == 1.0


@pytest.mark.parametrize(
    "error", [RedisConnectionError("down"), RedisError("boom"), TimeoutError()]
)
async def test_a_write_error_is_swallowed_and_counted(error, metrics):
    cache = ArticleCache(RaisingRedis(error), metrics=metrics)

    await cache.set_article(42, BODY, ttl_seconds=300)  # must not raise

    assert counter(metrics, "cache_errors_total", {"operation": "set"}) == 1.0


async def test_an_invalidation_error_does_not_raise(metrics):
    """A Redis failure after a committed write must not turn a 201 into a 500."""

    cache = ArticleCache(RaisingRedis(), metrics=metrics)

    await cache.invalidate_article(42)  # must not raise

    assert counter(metrics, "cache_errors_total", {"operation": "delete"}) == 1.0


async def test_a_generation_bump_error_does_not_raise(metrics):
    cache = ArticleCache(RaisingRedis(), metrics=metrics)

    await cache.bump_generations(author_id=5)  # must not raise

    assert counter(metrics, "cache_errors_total", {"operation": "incr"}) == 1.0


async def test_a_generation_read_error_degrades_to_generation_zero(metrics):
    cache = ArticleCache(RaisingRedis(), metrics=metrics)

    assert await cache.get_generations(author_id=5) == (0, 0)
    assert counter(metrics, "cache_errors_total", {"operation": "get"}) == 1.0


async def test_a_lock_error_lets_the_caller_load_the_database_itself(metrics):
    """Losing the lock machinery must cost a duplicate query, not a failed request."""

    cache = ArticleCache(RaisingRedis(), metrics=metrics)

    assert await cache.acquire_lock("article:42", ttl_seconds=2) is True
    assert counter(metrics, "cache_errors_total", {"operation": "lock"}) == 1.0


# --- disabled -------------------------------------------------------------------------------


async def test_a_disabled_cache_never_touches_redis(redis, metrics):
    cache = ArticleCache(redis, metrics=metrics, enabled=False)

    assert await cache.get_article(42) is None
    await cache.set_article(42, BODY, ttl_seconds=300)
    await cache.invalidate_article(42)
    await cache.bump_generations(author_id=5)

    assert redis.calls == []


async def test_a_disabled_cache_records_no_hit_or_miss(redis, metrics):
    """A bypassed cache has no hit ratio. Counting misses would make the graph read as a fault."""

    cache = ArticleCache(redis, metrics=metrics, enabled=False)
    await cache.get_article(42)

    assert counter(metrics, "cache_misses_total", {"entity": "article"}) == 0.0
    assert counter(metrics, "cache_errors_total", {"operation": "get"}) == 0.0


async def test_a_disabled_cache_never_holds_a_lock(redis, metrics):
    cache = ArticleCache(redis, metrics=metrics, enabled=False)

    assert await cache.acquire_lock("article:42", ttl_seconds=2) is True
    assert redis.calls == []


# --- peek -------------------------------------------------------------------------------------


async def test_peek_reads_without_counting_a_hit_or_miss(cache, redis, metrics):
    """The single-flight poll re-reads repeatedly; counting each one would fake the hit ratio."""

    redis.store["article:42"] = BODY

    assert await cache.peek("article:42") == BODY
    assert await cache.peek("article:99") is None
    assert counter(metrics, "cache_hits_total", {"entity": "article"}) == 0.0
    assert counter(metrics, "cache_misses_total", {"entity": "article"}) == 0.0


async def test_peek_degrades_to_none_on_failure(metrics):
    cache = ArticleCache(RaisingRedis(), metrics=metrics)

    assert await cache.peek("article:42") is None
    assert counter(metrics, "cache_errors_total", {"operation": "get"}) == 1.0


async def test_a_disabled_cache_peeks_at_nothing(redis, metrics):
    cache = ArticleCache(redis, metrics=metrics, enabled=False)

    assert await cache.peek("article:42") is None
    assert redis.calls == []


# --- degraded latch ----------------------------------------------------------------------------


async def test_one_failure_stops_every_later_call_in_the_same_request(metrics):
    """A blackholed Redis costs one timeout per request, not one per command.

    Without this, a cache-aside read pays the socket timeout four times over — get, lock,
    set, release — and a 2-second timeout becomes an 8-second request. The read still
    succeeds, which makes it worse: nothing fails, everything is slow.
    """
    redis = RaisingRedis()
    cache = ArticleCache(redis, metrics=metrics)

    assert await cache.get_article(42) is None
    await cache.set_article(42, BODY, ttl_seconds=300)
    assert await cache.acquire_lock("article:42", ttl_seconds=2) is True
    await cache.release_lock("article:42")
    await cache.invalidate_article(42)
    await cache.bump_generations(author_id=5)

    assert cache.degraded is True
    assert counter(metrics, "cache_errors_total", {"operation": "get"}) == 1.0
    assert counter(metrics, "cache_errors_total", {"operation": "set"}) == 0.0
    assert counter(metrics, "cache_errors_total", {"operation": "lock"}) == 0.0


async def test_a_degraded_cache_still_answers_reads_as_misses(metrics):
    cache = ArticleCache(RaisingRedis(), metrics=metrics)
    await cache.get_article(42)

    assert await cache.get_article(99) is None
    assert await cache.get_generations(author_id=5) == (0, 0)
    assert await cache.peek("article:99") is None


async def test_a_healthy_cache_is_not_degraded(cache):
    await cache.get_article(42)

    assert cache.degraded is False


async def test_the_latch_is_per_instance_not_per_process(redis, metrics):
    """The latch lives for one request. A later request must try Redis again."""

    failed = ArticleCache(RaisingRedis(), metrics=metrics)
    await failed.get_article(42)
    healthy = ArticleCache(redis, metrics=metrics)

    assert failed.degraded is True
    assert healthy.degraded is False
    await healthy.set_article(42, BODY, ttl_seconds=300)
    assert await healthy.get_article(42) == BODY
