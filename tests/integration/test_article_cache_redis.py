"""Cache-aside against a real Redis. Never mocks: ADR-0006 tests the cache client
against a container, because the failure modes worth catching are the driver's.

The degradation test is the important one. ADR-0004 makes Redis an optimization and never a
dependency, so a read must still succeed with the cache pointed at nothing.
"""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.cache.article_cache import ArticleCache
from app.cache.keys import article_key, author_generation_key, generation_key
from app.config import get_settings
from app.models import Article
from app.schemas.article import ArticleOut
from app.services.article_service import ArticleService
from tests.conftest import wipe_cache

pytestmark = pytest.mark.integration

T0 = datetime(2026, 7, 18, 12, 0, 0)


class SlowRepository:
    """A repository whose reads take long enough for a stampede to form."""

    def __init__(self, article: Article, delay: float = 0.0) -> None:
        self._article = article
        self.delay = delay
        self.get_calls = 0
        self.list_calls = 0

    async def get(self, article_id: int) -> Article | None:
        self.get_calls += 1
        if self.delay:
            import asyncio

            await asyncio.sleep(self.delay)
        return self._article if article_id == self._article.id else None

    async def list(self, *, limit, after=None, author_id=None) -> list[Article]:
        self.list_calls += 1
        return [self._article]


def make_article(article_id: int = 1, author_id: int = 7) -> Article:
    return Article(
        id=article_id,
        author_id=author_id,
        title="t",
        body="b",
        created_at=T0,
        updated_at=T0,
    )


@pytest_asyncio.fixture
async def redis(redis_available: None):
    """A dedicated client, so a failure here cannot leave the shared one half-closed."""
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    await wipe_cache()
    yield client
    await wipe_cache()
    await client.aclose()


@pytest_asyncio.fixture
async def cache(redis) -> ArticleCache:
    return ArticleCache(redis)


async def test_an_article_round_trips_through_redis(cache, redis):
    await cache.set_article(1, '{"id": 1}', ttl_seconds=300)

    assert await cache.get_article(1) == '{"id": 1}'
    assert await redis.exists(article_key(1)) == 1


async def test_the_stored_ttl_lands_inside_the_jitter_band(redis):
    """A real SETEX, read back with a real TTL — the fake cannot prove this."""
    service = ArticleService(
        SlowRepository(make_article()),  # type: ignore[arg-type]
        ArticleCache(redis),
        article_ttl_seconds=300,
        ttl_jitter=0.2,
    )
    await service.get(1)

    ttl = await redis.ttl(article_key(1))
    assert 240 <= ttl <= 360


async def test_a_list_page_ttl_is_shorter_than_an_article_ttl(redis):
    """Pages go stale faster than rows do, and the TTLs must reflect that."""
    service = ArticleService(
        SlowRepository(make_article()),  # type: ignore[arg-type]
        ArticleCache(redis),
        article_ttl_seconds=300,
        list_ttl_seconds=60,
        ttl_jitter=0.0,
    )
    await service.get(1)
    await service.list_articles(limit=20)

    page_keys = [key async for key in redis.scan_iter(match="articles:list:g*")]
    assert page_keys
    assert await redis.ttl(page_keys[0]) <= await redis.ttl(article_key(1))


async def test_concurrent_misses_cause_one_database_read(redis):
    """Bottleneck B1. Ten readers, one hot key, one query — through real Redis locks."""
    import asyncio

    repo = SlowRepository(make_article(), delay=0.15)
    service = ArticleService(repo, ArticleCache(redis))  # type: ignore[arg-type]

    results = await asyncio.gather(*(service.get(1) for _ in range(10)))

    assert repo.get_calls == 1
    assert all(result.id == 1 for result in results)


async def test_the_single_flight_lock_is_released_after_the_load(redis):
    """A lock left behind would serialize every later reader behind its full TTL."""
    service = ArticleService(
        SlowRepository(make_article()),  # type: ignore[arg-type]
        ArticleCache(redis),
    )
    await service.get(1)

    assert await redis.exists("lock:article:1") == 0


async def test_the_lock_is_released_even_when_the_load_fails(redis):
    """An exception must not leave the key locked for the whole lock TTL."""
    from app.services.exceptions import ArticleNotFoundError

    service = ArticleService(
        SlowRepository(make_article()),  # type: ignore[arg-type]
        ArticleCache(redis),
    )
    with pytest.raises(ArticleNotFoundError):
        await service.get(404)

    assert await redis.exists("lock:article:404") == 0


async def test_generations_start_at_zero_and_advance(cache, redis):
    assert await cache.get_generations(author_id=7) == (0, 0)

    await cache.bump_generations(author_id=7)

    assert await cache.get_generations(author_id=7) == (1, 1)
    assert await redis.get(generation_key()) == "1"
    assert await redis.get(author_generation_key(7)) == "1"


async def test_a_bump_makes_the_previous_page_unreachable(redis):
    """One INCR invalidates every cached page under the old generation."""
    repo = SlowRepository(make_article())
    service = ArticleService(repo, ArticleCache(redis))  # type: ignore[arg-type]
    await service.list_articles(limit=20)
    await service.list_articles(limit=20)
    assert repo.list_calls == 1

    await ArticleCache(redis).bump_generations(author_id=None)
    await service.list_articles(limit=20)

    assert repo.list_calls == 2


async def test_a_write_by_one_author_spares_another_authors_pages(redis):
    """The property the per-author counter buys, proven end to end against Redis."""
    repo = SlowRepository(make_article(author_id=7))
    service = ArticleService(repo, ArticleCache(redis))  # type: ignore[arg-type]
    await service.list_articles(limit=20, author_id=7)
    calls_after_population = repo.list_calls

    await ArticleCache(redis).bump_generations(author_id=9)  # a different author writes
    await service.list_articles(limit=20, author_id=7)

    assert repo.list_calls == calls_after_population


async def test_reads_still_succeed_when_redis_is_unreachable():
    """The degradation guarantee: the cache is an optimization, never a dependency."""
    unreachable = Redis.from_url(
        "redis://127.0.0.1:6390/0",
        decode_responses=True,
        socket_timeout=0.25,
        socket_connect_timeout=0.25,
    )
    cache = ArticleCache(unreachable)
    repo = SlowRepository(make_article())
    service = ArticleService(repo, cache)  # type: ignore[arg-type]

    try:
        assert (await service.get(1)).id == 1
        items, _ = await service.list_articles(limit=20)
        assert len(items) == 1
    finally:
        await unreachable.aclose()


async def test_a_cached_body_deserializes_to_the_same_dto(redis):
    """A round trip through JSON must not lose microsecond precision on the CAS token."""
    article = make_article()
    article.updated_at = T0 + timedelta(microseconds=123_456)
    service = ArticleService(SlowRepository(article), ArticleCache(redis))  # type: ignore[arg-type]

    live = await service.get(1)
    cached = await service.get(1)

    assert cached == live
    assert cached.updated_at == ArticleOut.model_validate(article).updated_at
