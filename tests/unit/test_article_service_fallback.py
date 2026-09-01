"""Unit tests for what the service returns while a dependency is down. No I/O.

The guard turns a dead dependency into a domain error (`app/resilience/guard.py`), and the
envelope turns that into a 503 or a 504. This module tests the layer between them: the service
must let a bounded failure through unchanged, and it must serve what it can from the cache
first. Two rules are under test.

A cached answer beats an outage. MySQL being down does not make a cached article wrong, so a
read that the cache can satisfy must never reach the repository.

A refusal is not a crash. When nothing can answer, the service raises the same
`DependencyUnavailableError` or `UpstreamTimeoutError` the repository raised. Wrapping it, or
letting it become a 500, would tell the caller the server is broken when the server is working
exactly as designed.
"""

from datetime import datetime

import pytest
from prometheus_client import CollectorRegistry

from app.cache.article_cache import ArticleCache
from app.cache.keys import article_key
from app.models import Article
from app.observability.metrics import Metrics, build_metrics
from app.schemas.article import ArticleOut
from app.services.article_service import ArticleService
from app.services.exceptions import DependencyUnavailableError, UpstreamTimeoutError
from tests.unit.test_article_cache import FakeRedis, RaisingRedis
from tests.unit.test_article_service_caching import T0, CountingRepository, make_user

RETRY_AFTER = 4.5


class DownRepository(CountingRepository):
    """Every statement fails the way a guarded repository fails when MySQL is gone.

    It counts the calls it refuses, so a test can tell "the repository said no" from "the
    repository was never asked", which is the whole difference between a fallback and a retry.
    """

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self.create_calls = 0
        self.update_calls = 0
        self._error = error or DependencyUnavailableError(
            "mysql is unavailable", retry_after=RETRY_AFTER
        )

    async def create(self, **kwargs):
        self.create_calls += 1
        raise self._error

    async def get(self, article_id):
        self.get_calls += 1
        raise self._error

    async def list(self, **kwargs):
        self.list_calls += 1
        raise self._error

    async def update_cas(self, *args, **kwargs):
        self.update_calls += 1
        raise self._error

    async def soft_delete_cas(self, *args, **kwargs):
        self.update_calls += 1
        raise self._error


@pytest.fixture
def metrics() -> Metrics:
    return build_metrics(CollectorRegistry())


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


def cached_article(redis: FakeRedis, article_id: int = 1) -> ArticleOut:
    """Put one article body in the cache and return the shape a hit must produce."""
    dto = ArticleOut.model_validate(
        Article(
            id=article_id,
            author_id=1,
            title="cached",
            body="served from the cache",
            created_at=T0,
            updated_at=T0,
        )
    )
    redis.store[article_key(article_id)] = dto.model_dump_json()
    return dto


# --- MySQL down --------------------------------------------------------------------------


async def test_a_cache_hit_survives_a_database_outage(redis: FakeRedis, metrics: Metrics) -> None:
    repo = DownRepository()
    service = ArticleService(repo, cache=ArticleCache(redis, metrics=metrics))  # type: ignore[arg-type]
    expected = cached_article(redis)

    assert await service.get(1) == expected
    # The point of the cache under failure: the outage is invisible for anything already in it.
    assert repo.get_calls == 0


async def test_a_cache_miss_during_a_database_outage_refuses_rather_than_fails(
    redis: FakeRedis, metrics: Metrics
) -> None:
    repo = DownRepository()
    service = ArticleService(repo, cache=ArticleCache(redis, metrics=metrics))  # type: ignore[arg-type]

    with pytest.raises(DependencyUnavailableError) as raised:
        await service.get(404)

    # Unwrapped, so the envelope answers 503 with a Retry-After the caller can act on.
    assert raised.value.retry_after == RETRY_AFTER


async def test_a_slow_database_reaches_the_caller_as_a_timeout(
    redis: FakeRedis, metrics: Metrics
) -> None:
    repo = DownRepository(UpstreamTimeoutError("mysql did not answer", retry_after=1.0))
    service = ArticleService(repo, cache=ArticleCache(redis, metrics=metrics))  # type: ignore[arg-type]

    # A separate type, because a slow dependency and a refused one need different fixes.
    with pytest.raises(UpstreamTimeoutError):
        await service.get(1)


async def test_a_list_page_during_a_database_outage_refuses(
    redis: FakeRedis, metrics: Metrics
) -> None:
    repo = DownRepository()
    service = ArticleService(repo, cache=ArticleCache(redis, metrics=metrics))  # type: ignore[arg-type]

    with pytest.raises(DependencyUnavailableError):
        await service.list_articles(limit=10)


# --- writes with the circuit open ---------------------------------------------------------


async def test_a_create_with_the_circuit_open_refuses(redis: FakeRedis, metrics: Metrics) -> None:
    repo = DownRepository()
    service = ArticleService(repo, cache=ArticleCache(redis, metrics=metrics))  # type: ignore[arg-type]

    with pytest.raises(DependencyUnavailableError):
        await service.create(make_user(1), title="t", body="b")


async def test_an_update_with_the_circuit_open_never_reaches_the_statement(
    redis: FakeRedis, metrics: Metrics
) -> None:
    repo = DownRepository()
    service = ArticleService(repo, cache=ArticleCache(redis, metrics=metrics))  # type: ignore[arg-type]

    with pytest.raises(DependencyUnavailableError):
        await service.update(
            make_user(1), 1, title="t", body="b", expected_updated_at=datetime(2026, 1, 1)
        )

    # The load refused first, so the compare-and-set never ran. A write must never reach a
    # dependency the breaker has already declared down.
    assert repo.update_calls == 0


async def test_a_write_never_serves_the_cache_instead_of_the_database(
    redis: FakeRedis, metrics: Metrics
) -> None:
    repo = DownRepository()
    service = ArticleService(repo, cache=ArticleCache(redis, metrics=metrics))  # type: ignore[arg-type]
    cached_article(redis)

    # Ownership and compare-and-set read MySQL, never the cache. A cached copy must not let a
    # write proceed on stale `author_id` or stale `updated_at` while the database is gone.
    with pytest.raises(DependencyUnavailableError):
        await service.delete(make_user(1), 1, expected_updated_at=T0)


# --- Redis down, MySQL up -----------------------------------------------------------------


async def test_redis_down_still_serves_a_read(metrics: Metrics) -> None:
    repo = CountingRepository()
    service = ArticleService(repo, cache=ArticleCache(RaisingRedis(), metrics=metrics))  # type: ignore[arg-type]
    created = await repo.create(author_id=1, title="t", body="b")

    dto = await service.get(created.id)

    assert dto.id == created.id
    # The read fell through to MySQL. ADR-0004: the cache is an optimization, never a dependency.
    assert repo.get_calls == 1


async def test_redis_down_still_serves_a_list_page(metrics: Metrics) -> None:
    repo = CountingRepository()
    service = ArticleService(repo, cache=ArticleCache(RaisingRedis(), metrics=metrics))  # type: ignore[arg-type]
    await repo.create(author_id=1, title="t", body="b")

    items, cursor = await service.list_articles(limit=10)

    assert len(items) == 1
    assert cursor is None


async def test_redis_down_does_not_block_a_write(metrics: Metrics) -> None:
    repo = CountingRepository()
    service = ArticleService(repo, cache=ArticleCache(RaisingRedis(), metrics=metrics))  # type: ignore[arg-type]

    dto = await service.create(make_user(1), title="t", body="b")

    # Invalidation could not run, and the write still succeeded. The cost is a stale page for
    # one TTL, which is the trade ADR-0004 accepts.
    assert dto.title == "t"
