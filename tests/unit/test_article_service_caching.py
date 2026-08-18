"""Unit tests for the cache-aside behavior of ArticleService. No I/O.

The rule the whole feature is built around: authorization and compare-and-set never read
the cache. A stale cached ``author_id`` would decide ownership on stale data, and a stale
``updated_at`` would break the optimistic-concurrency precondition. ``get`` is cache-aside;
``_load`` always reads the database, and the write paths use only ``_load``.
"""

import asyncio
import itertools
from datetime import datetime, timedelta

import pytest
from prometheus_client import CollectorRegistry

from app.cache.article_cache import ArticleCache
from app.cache.keys import article_key
from app.models import Article, Role, User
from app.observability.metrics import Metrics, build_metrics
from app.schemas.article import ArticleOut
from app.services.article_service import ArticleService
from app.services.exceptions import ArticleNotFoundError
from tests.unit.test_article_cache import FakeRedis, RaisingRedis

T0 = datetime(2026, 7, 18, 12, 0, 0)


class CountingRepository:
    """In-memory repository that records how often the database was actually read."""

    def __init__(self) -> None:
        self.rows: dict[int, Article] = {}
        self.get_calls = 0
        self.list_calls = 0
        self._ids = itertools.count(1)
        self._clock = itertools.count(1)
        self.load_delay = 0.0

    def _now(self) -> datetime:
        return T0 + timedelta(seconds=next(self._clock))

    async def create(self, *, author_id: int, title: str, body: str) -> Article:
        now = self._now()
        article = Article(
            id=next(self._ids),
            author_id=author_id,
            title=title,
            body=body,
            created_at=now,
            updated_at=now,
        )
        self.rows[article.id] = article
        return article

    async def get(self, article_id: int) -> Article | None:
        self.get_calls += 1
        if self.load_delay:
            await asyncio.sleep(self.load_delay)
        article = self.rows.get(article_id)
        if article is None or article.deleted_at is not None:
            return None
        return article

    async def list(self, *, limit, after=None, author_id=None) -> list[Article]:
        self.list_calls += 1
        rows = [
            row
            for row in self.rows.values()
            if row.deleted_at is None and (author_id is None or row.author_id == author_id)
        ]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        if after is not None:
            rows = [r for r in rows if (r.created_at, r.id) < after]
        return rows[:limit]

    async def update_cas(self, article_id, expected_updated_at, *, title, body) -> int:
        article = self.rows.get(article_id)
        if (
            article is None
            or article.deleted_at is not None
            or article.updated_at != expected_updated_at
        ):
            return 0
        article.title, article.body = title, body
        article.updated_at = self._now()
        return 1

    async def soft_delete_cas(self, article_id, expected_updated_at) -> int:
        article = self.rows.get(article_id)
        if (
            article is None
            or article.deleted_at is not None
            or article.updated_at != expected_updated_at
        ):
            return 0
        article.deleted_at = self._now()
        return 1


def make_user(user_id: int, role: str = "user") -> User:
    return User(
        id=user_id,
        email=f"u{user_id}@example.com",
        password_hash="x",
        role=Role(id=1, name=role),
    )


@pytest.fixture
def metrics() -> Metrics:
    return build_metrics(CollectorRegistry())


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def repo() -> CountingRepository:
    return CountingRepository()


@pytest.fixture
def cache(redis: FakeRedis, metrics: Metrics) -> ArticleCache:
    return ArticleCache(redis, metrics=metrics)


@pytest.fixture
def service(repo: CountingRepository, cache: ArticleCache) -> ArticleService:
    return ArticleService(repo, cache=cache)  # type: ignore[arg-type]


@pytest.fixture
def owner() -> User:
    return make_user(1)


def counter(m: Metrics, name: str, labels: dict[str, str]) -> float:
    return m.registry.get_sample_value(name, labels) or 0.0


# --- article detail ---------------------------------------------------------------------------


async def test_a_miss_populates_the_cache(service, repo, redis, owner):
    created = await repo.create(author_id=owner.id, title="t", body="b")

    await service.get(created.id)

    assert article_key(created.id) in redis.store


async def test_a_hit_does_not_touch_the_repository(service, repo, owner):
    created = await repo.create(author_id=owner.id, title="t", body="b")
    await service.get(created.id)
    before = repo.get_calls

    await service.get(created.id)

    assert repo.get_calls == before


async def test_a_hit_returns_the_same_body_as_the_miss(service, repo, owner):
    created = await repo.create(author_id=owner.id, title="t", body="b")

    first = await service.get(created.id)
    second = await service.get(created.id)

    assert first == second


async def test_get_returns_a_dto_not_an_orm_row(service, repo, owner):
    """The cache stores JSON, so both paths must return the same type or they diverge."""

    created = await repo.create(author_id=owner.id, title="t", body="b")

    assert isinstance(await service.get(created.id), ArticleOut)


async def test_a_cache_failure_falls_through_to_the_repository(repo, metrics, owner):
    service = ArticleService(repo, cache=ArticleCache(RaisingRedis(), metrics=metrics))  # type: ignore[arg-type]
    created = await repo.create(author_id=owner.id, title="t", body="b")

    assert (await service.get(created.id)).id == created.id
    assert counter(metrics, "cache_errors_total", {"operation": "get"}) >= 1.0


async def test_a_corrupt_cached_body_is_treated_as_a_miss(service, repo, redis, owner):
    """A body written by an older schema must not raise; it must be reloaded and replaced."""

    created = await repo.create(author_id=owner.id, title="t", body="b")
    redis.store[article_key(created.id)] = "{not json"

    assert (await service.get(created.id)).title == "t"


async def test_a_missing_article_is_not_cached(service, redis):
    with pytest.raises(ArticleNotFoundError):
        await service.get(404)

    assert article_key(404) not in redis.store


# --- the rule: writes never read the cache ------------------------------------------------------


async def test_update_never_reads_the_cache(service, repo, redis, owner):
    """A stale cached author_id would decide ownership. The write path must read MySQL."""

    created = await repo.create(author_id=owner.id, title="t", body="b")
    await service.get(created.id)  # populate
    reads_before = [call for call in redis.calls if call[0] == "get"]

    await service.update(
        owner, created.id, title="t2", body="b2", expected_updated_at=created.updated_at
    )

    assert [call for call in redis.calls if call[0] == "get"] == reads_before


async def test_delete_never_reads_the_cache(service, repo, redis, owner):
    created = await repo.create(author_id=owner.id, title="t", body="b")
    await service.get(created.id)
    reads_before = [call for call in redis.calls if call[0] == "get"]

    await service.delete(owner, created.id, expected_updated_at=created.updated_at)

    assert [call for call in redis.calls if call[0] == "get"] == reads_before


async def test_update_authorizes_against_the_database_not_a_stale_cache(
    service, repo, redis, owner
):
    """Poison the cache with another author, then confirm the owner is still allowed through."""

    created = await repo.create(author_id=owner.id, title="t", body="b")
    poisoned = ArticleOut.model_validate(created).model_copy(update={"author_id": 999})
    redis.store[article_key(created.id)] = poisoned.model_dump_json()

    updated = await service.update(
        owner, created.id, title="t2", body="b2", expected_updated_at=created.updated_at
    )

    assert updated.title == "t2"


async def test_update_uses_the_database_updated_at_for_the_cas_precondition(
    service, repo, redis, owner
):
    """A stale cached updated_at must not become the token the CAS compares against."""

    created = await repo.create(author_id=owner.id, title="t", body="b")
    stale = ArticleOut.model_validate(created).model_copy(
        update={"updated_at": created.updated_at - timedelta(hours=1)}
    )
    redis.store[article_key(created.id)] = stale.model_dump_json()

    updated = await service.update(
        owner, created.id, title="t2", body="b2", expected_updated_at=created.updated_at
    )

    assert updated.updated_at != stale.updated_at


# --- list pages -----------------------------------------------------------------------------


async def test_a_list_miss_populates_and_a_hit_skips_the_repository(service, repo, owner):
    await repo.create(author_id=owner.id, title="t", body="b")

    first = await service.list_articles(limit=20)
    before = repo.list_calls
    second = await service.list_articles(limit=20)

    assert repo.list_calls == before
    assert first == second


async def test_a_list_hit_counts_under_the_list_entity(service, repo, metrics, owner):
    await repo.create(author_id=owner.id, title="t", body="b")
    await service.list_articles(limit=20)
    await service.list_articles(limit=20)

    assert counter(metrics, "cache_hits_total", {"entity": "list"}) == 1.0
    assert counter(metrics, "cache_misses_total", {"entity": "list"}) == 1.0


async def test_a_list_page_survives_across_different_limits_as_separate_entries(
    service, repo, owner
):
    await repo.create(author_id=owner.id, title="t", body="b")
    await service.list_articles(limit=20)
    before = repo.list_calls

    await service.list_articles(limit=5)

    assert repo.list_calls == before + 1


async def test_author_filtered_pages_are_cached_separately_from_unfiltered(service, repo, owner):
    await repo.create(author_id=owner.id, title="t", body="b")
    await service.list_articles(limit=20)
    before = repo.list_calls

    await service.list_articles(limit=20, author_id=owner.id)

    assert repo.list_calls == before + 1


async def test_a_cached_list_page_round_trips_the_next_cursor(service, repo, owner):
    for _ in range(3):
        await repo.create(author_id=owner.id, title="t", body="b")

    _, live_next = await service.list_articles(limit=2)
    _, cached_next = await service.list_articles(limit=2)

    assert live_next is not None
    assert cached_next == live_next


async def test_a_corrupt_cached_list_page_is_treated_as_a_miss(service, repo, redis, owner):
    await repo.create(author_id=owner.id, title="t", body="b")
    await service.list_articles(limit=20)
    for key in [k for k in redis.store if k.startswith("articles:list:g")]:
        redis.store[key] = "{not json"

    items, _ = await service.list_articles(limit=20)

    assert len(items) == 1


async def test_a_generation_bump_makes_the_cached_page_unreachable(service, repo, cache, owner):
    await repo.create(author_id=owner.id, title="t", body="b")
    await service.list_articles(limit=20)
    before = repo.list_calls

    await cache.bump_generations(author_id=None)

    await service.list_articles(limit=20)
    assert repo.list_calls == before + 1


# --- single flight ------------------------------------------------------------------------------


async def test_concurrent_misses_cause_one_database_load(service, repo, owner):
    """Bottleneck B1: without single-flight every concurrent miss becomes its own query."""

    created = await repo.create(author_id=owner.id, title="t", body="b")
    repo.load_delay = 0.05
    repo.get_calls = 0

    await asyncio.gather(*(service.get(created.id) for _ in range(10)))

    assert repo.get_calls == 1


async def test_a_loser_falls_through_rather_than_hanging(repo, redis, metrics, owner):
    """A lock nobody releases must cost a duplicate query, never a stuck request."""

    created = await repo.create(author_id=owner.id, title="t", body="b")
    cache = ArticleCache(redis, metrics=metrics)
    await cache.acquire_lock(article_key(created.id), ttl_seconds=60)  # orphaned lock
    service = ArticleService(repo, cache=cache, lock_timeout_seconds=0.1)  # type: ignore[arg-type]

    result = await asyncio.wait_for(service.get(created.id), timeout=2.0)

    assert result.id == created.id


# --- disabled -------------------------------------------------------------------------------


async def test_a_disabled_cache_reads_the_database_every_time(repo, redis, metrics, owner):
    cache = ArticleCache(redis, metrics=metrics, enabled=False)
    service = ArticleService(repo, cache=cache)  # type: ignore[arg-type]
    created = await repo.create(author_id=owner.id, title="t", body="b")

    await service.get(created.id)
    await service.get(created.id)

    assert repo.get_calls == 2
    assert redis.calls == []


async def test_the_service_works_with_no_cache_supplied(repo, owner):
    """The constructor seam is optional; the unit suite constructs the service without one."""

    service = ArticleService(repo)  # type: ignore[arg-type]
    created = await repo.create(author_id=owner.id, title="t", body="b")

    assert (await service.get(created.id)).id == created.id
