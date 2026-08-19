"""Unit tests for cache invalidation on write. No I/O.

Two properties matter. The commands must be exactly right — a write emits one `DEL` and the
one or two `INCR`s it can affect, and no more — and a Redis failure must never reach the
client, because the row is already committed by the time invalidation runs.
"""

import pytest
from prometheus_client import CollectorRegistry

from app.cache.article_cache import ArticleCache
from app.cache.keys import article_key, author_generation_key, generation_key
from app.db.after_commit import drain_after_commit
from app.observability.metrics import Metrics, build_metrics
from app.services.article_service import ArticleService
from tests.unit.test_article_cache import FakeRedis, RaisingRedis
from tests.unit.test_article_service_caching import CountingRepository, make_user


class FakeSession:
    def __init__(self) -> None:
        self.info: dict = {}


@pytest.fixture
def metrics() -> Metrics:
    return build_metrics(CollectorRegistry())


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def repo() -> CountingRepository:
    return CountingRepository()


@pytest.fixture
def owner():
    return make_user(1)


def build(repo, redis, session, metrics) -> ArticleService:
    return ArticleService(
        repo,  # type: ignore[arg-type]
        ArticleCache(redis, metrics=metrics),
        session=session,
    )


def commands(redis: FakeRedis) -> list[tuple]:
    """Only the mutating commands, so an assertion reads as the write's effect."""
    return [call for call in redis.calls if call[0] in {"delete", "incr"}]


def counter(m: Metrics, name: str, labels: dict[str, str]) -> float:
    return m.registry.get_sample_value(name, labels) or 0.0


# --- nothing runs before the commit -------------------------------------------------------------


async def test_a_write_invalidates_nothing_before_the_commit(repo, redis, session, metrics, owner):
    """The row is not durable yet. A reader that repopulated now would cache the old value."""
    service = build(repo, redis, session, metrics)

    await service.create(owner, title="t", body="b")

    assert commands(redis) == []


async def test_a_rolled_back_write_invalidates_nothing(repo, redis, session, metrics, owner):
    """No commit, no new value, nothing to invalidate to."""
    from app.db.after_commit import discard_after_commit

    service = build(repo, redis, session, metrics)
    await service.create(owner, title="t", body="b")

    discard_after_commit(session)
    await drain_after_commit(session)

    assert commands(redis) == []


# --- the rules -----------------------------------------------------------------------------------


async def test_a_create_bumps_both_generations_and_deletes_nothing(
    repo, redis, session, metrics, owner
):
    """A new article has no cached body of its own; only the pages that must now include it."""
    service = build(repo, redis, session, metrics)
    await service.create(owner, title="t", body="b")

    await drain_after_commit(session)

    assert commands(redis) == [
        ("incr", generation_key()),
        ("incr", author_generation_key(owner.id)),
    ]


async def test_an_update_deletes_the_body_and_bumps_both_generations(
    repo, redis, session, metrics, owner
):
    service = build(repo, redis, session, metrics)
    created = await repo.create(author_id=owner.id, title="t", body="b")
    await service.update(
        owner, created.id, title="t2", body="b2", expected_updated_at=created.updated_at
    )

    await drain_after_commit(session)

    assert commands(redis) == [
        ("delete", article_key(created.id)),
        ("incr", generation_key()),
        ("incr", author_generation_key(owner.id)),
    ]


async def test_a_delete_deletes_the_body_and_bumps_both_generations(
    repo, redis, session, metrics, owner
):
    service = build(repo, redis, session, metrics)
    created = await repo.create(author_id=owner.id, title="t", body="b")
    await service.delete(owner, created.id, expected_updated_at=created.updated_at)

    await drain_after_commit(session)

    assert commands(redis) == [
        ("delete", article_key(created.id)),
        ("incr", generation_key()),
        ("incr", author_generation_key(owner.id)),
    ]


async def test_an_admin_write_bumps_the_authors_generation_not_the_admins(
    repo, redis, session, metrics, owner
):
    """The pages that change are the author's, so the author's counter is the one to move."""

    service = build(repo, redis, session, metrics)
    created = await repo.create(author_id=owner.id, title="t", body="b")
    admin = make_user(99, role="admin")

    await service.update(
        admin, created.id, title="t2", body="b2", expected_updated_at=created.updated_at
    )
    await drain_after_commit(session)

    assert ("incr", author_generation_key(owner.id)) in commands(redis)
    assert ("incr", author_generation_key(admin.id)) not in commands(redis)


async def test_a_write_leaves_another_authors_generation_untouched(
    repo, redis, session, metrics, owner
):
    """The property the per-author counter buys, stated as a test."""
    redis.store[author_generation_key(9)] = "4"
    service = build(repo, redis, session, metrics)
    await service.create(owner, title="t", body="b")
    await drain_after_commit(session)

    assert redis.store[author_generation_key(9)] == "4"


async def test_a_failed_write_invalidates_nothing(repo, redis, session, metrics, owner):
    """A 403 changed no row. Busting the cache for it would be pure loss."""
    from app.services.exceptions import ForbiddenError

    service = build(repo, redis, session, metrics)
    created = await repo.create(author_id=owner.id, title="t", body="b")

    with pytest.raises(ForbiddenError):
        await service.update(
            make_user(2),
            created.id,
            title="x",
            body="x",
            expected_updated_at=created.updated_at,
        )
    await drain_after_commit(session)

    assert commands(redis) == []


# --- fail-open ------------------------------------------------------------------------------------


async def test_a_redis_failure_during_invalidation_does_not_fail_the_write(
    repo, session, metrics, owner
):
    """The row is committed. The client is owed its 201 whatever Redis is doing."""
    service = ArticleService(
        repo,  # type: ignore[arg-type]
        ArticleCache(RaisingRedis(), metrics=metrics),
        session=session,
    )
    result = await service.create(owner, title="t", body="b")

    await drain_after_commit(session)  # must not raise

    assert result.title == "t"
    assert counter(metrics, "cache_errors_total", {"operation": "incr"}) == 1.0


async def test_the_service_still_works_without_a_session(repo, redis, metrics, owner):
    """The queue is an optional seam."""
    service = ArticleService(repo, ArticleCache(redis, metrics=metrics))  # type: ignore[arg-type]

    result = await service.create(owner, title="t", body="b")

    assert result.title == "t"


# --- read-your-writes through the cache -----------------------------------------------------


async def test_a_read_after_an_update_does_not_return_the_cached_body(
    repo, redis, session, metrics, owner
):
    service = build(repo, redis, session, metrics)
    created = await repo.create(author_id=owner.id, title="t", body="b")
    await service.get(created.id)  # populate

    await service.update(
        owner, created.id, title="t2", body="b2", expected_updated_at=created.updated_at
    )
    await drain_after_commit(session)

    assert (await service.get(created.id)).title == "t2"


async def test_a_read_after_a_delete_is_a_404_not_a_cached_body(
    repo, redis, session, metrics, owner
):
    from app.services.exceptions import ArticleNotFoundError

    service = build(repo, redis, session, metrics)
    created = await repo.create(author_id=owner.id, title="t", body="b")
    await service.get(created.id)

    await service.delete(owner, created.id, expected_updated_at=created.updated_at)
    await drain_after_commit(session)

    with pytest.raises(ArticleNotFoundError):
        await service.get(created.id)


async def test_a_cached_page_survives_a_write_by_a_different_author(
    repo, redis, session, metrics, owner
):
    service = build(repo, redis, session, metrics)
    await repo.create(author_id=owner.id, title="t", body="b")
    await service.list_articles(limit=20, author_id=owner.id)
    calls_after_population = repo.list_calls

    other = make_user(2)
    await service.create(other, title="theirs", body="b")
    await drain_after_commit(session)

    await service.list_articles(limit=20, author_id=owner.id)
    assert repo.list_calls == calls_after_population
