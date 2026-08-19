"""Invalidation must run after the commit, against a real database and a real Redis.

``get_session`` commits in its teardown, after the handler returns. An invalidation that ran
inline would run before the row was durable, and a concurrent reader could then miss, read the
pre-commit row, and repopulate the cache with the old value — which would survive its full TTL
with no error and no metric. That ordering cannot be observed with a fake session, so it is
pinned here.
"""

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from app.cache.keys import article_key, author_generation_key, generation_key
from app.config import get_settings
from app.db.after_commit import after_commit
from app.db.session import get_session

pytestmark = pytest.mark.integration

BASE = "/v1/articles"
PAYLOAD = {"title": "A new title", "body": "New body text."}


@pytest.fixture
async def redis(redis_available: None):
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    yield client
    await client.aclose()


# --- the ordering itself --------------------------------------------------------------------


async def test_the_callback_runs_after_the_row_is_visible_to_another_connection(
    clean_db: None, db_available: None
):
    """The point of the whole queue: at callback time the write must be durable.

    The callback opens its own session, which cannot see an uncommitted row on another
    connection. If it finds the row, the commit has landed.
    """
    from app.db.session import get_sessionmaker

    seen = []

    async def check_visibility() -> None:
        async with get_sessionmaker()() as probe:
            count = await probe.scalar(text("SELECT COUNT(*) FROM roles WHERE name = 'user'"))
            seen.append(count)

    generator = get_session()
    session = await anext(generator)
    after_commit(session, check_visibility)
    await session.execute(text("SELECT 1"))
    with pytest.raises(StopAsyncIteration):
        await anext(generator)  # commits, then drains

    assert seen == [1]


async def test_a_rollback_discards_the_queue(clean_db: None, db_available: None):
    """No commit, no new value, nothing to invalidate to."""
    ran = []
    generator = get_session()
    session = await anext(generator)
    after_commit(session, lambda: ran.append("x"))

    with pytest.raises(RuntimeError):
        await generator.athrow(RuntimeError("handler blew up"))

    assert ran == []


async def test_a_failing_callback_does_not_break_the_request(clean_db: None, db_available: None):
    """The commit already succeeded; a cache error must not become a 500."""

    async def boom() -> None:
        raise RuntimeError("redis is down")

    generator = get_session()
    session = await anext(generator)
    after_commit(session, boom)
    with pytest.raises(StopAsyncIteration):
        await anext(generator)  # must not raise RuntimeError


# --- end to end over HTTP -----------------------------------------------------------------------


async def test_an_update_clears_the_cached_body(
    client, clean_db: None, clean_cache: None, redis, user_factory, article_factory, auth_headers
):
    user = await user_factory()
    article = await article_factory(author=user)
    await client.get(f"{BASE}/{article.id}")
    assert await redis.exists(article_key(article.id)) == 1

    response = await client.put(
        f"{BASE}/{article.id}",
        json=PAYLOAD,
        headers={**auth_headers(user), "If-Match": article.updated_at.isoformat()},
    )

    assert response.status_code == 200
    assert await redis.exists(article_key(article.id)) == 0


async def test_a_delete_clears_the_cached_body(
    client, clean_db: None, clean_cache: None, redis, user_factory, article_factory, auth_headers
):
    user = await user_factory()
    article = await article_factory(author=user)
    await client.get(f"{BASE}/{article.id}")

    response = await client.delete(
        f"{BASE}/{article.id}",
        headers={**auth_headers(user), "If-Match": article.updated_at.isoformat()},
    )

    assert response.status_code == 204
    assert await redis.exists(article_key(article.id)) == 0
    assert (await client.get(f"{BASE}/{article.id}")).status_code == 404


async def test_a_create_advances_both_generations(
    client, clean_db: None, clean_cache: None, redis, user_factory, auth_headers
):
    user = await user_factory()

    response = await client.post(BASE, json=PAYLOAD, headers=auth_headers(user))

    assert response.status_code == 201
    assert await redis.get(generation_key()) == "1"
    assert await redis.get(author_generation_key(user.id)) == "1"


async def test_a_rejected_write_advances_no_generation(
    client, clean_db: None, clean_cache: None, redis, user_factory, article_factory, auth_headers
):
    """A 403 changed no row. Busting the cache for it would be pure loss."""
    owner = await user_factory()
    intruder = await user_factory()
    article = await article_factory(author=owner)

    response = await client.put(
        f"{BASE}/{article.id}",
        json=PAYLOAD,
        headers={**auth_headers(intruder), "If-Match": article.updated_at.isoformat()},
    )

    assert response.status_code == 403
    assert await redis.get(generation_key()) is None


async def test_a_stale_precondition_advances_no_generation(
    client, clean_db: None, clean_cache: None, redis, user_factory, article_factory, auth_headers
):
    """A 409 rolled back. Nothing changed, so nothing is invalidated."""
    from datetime import timedelta

    user = await user_factory()
    article = await article_factory(author=user)
    stale = (article.updated_at - timedelta(hours=1)).isoformat()

    response = await client.put(
        f"{BASE}/{article.id}",
        json=PAYLOAD,
        headers={**auth_headers(user), "If-Match": stale},
    )

    assert response.status_code == 409
    assert await redis.get(generation_key()) is None


async def test_a_write_by_one_author_spares_another_authors_generation(
    client, clean_db: None, clean_cache: None, redis, user_factory, auth_headers
):
    author = await user_factory()
    other = await user_factory()

    await client.post(BASE, json=PAYLOAD, headers=auth_headers(author))

    assert await redis.get(author_generation_key(author.id)) == "1"
    assert await redis.get(author_generation_key(other.id)) is None
