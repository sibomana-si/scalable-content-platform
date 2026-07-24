"""
FR-005: paginated/filtered public reads.

Keyset pagination on '(created_at, id)': opaque cursor, bounded page size,
deterministic newest-first order, no duplicates or skips across a full walk, including
'created_at' ties spanning page boundaries. Filtering is by 'author' only.
All list reads are anonymous (no auth header anywhere in this module).
"""

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

BASE = "/v1/articles"
T0 = datetime(2026, 7, 1, 12, 0, 0)


async def _walk(client, limit: int, author: int | None = None) -> list[dict]:
    """Follow the cursor chain to exhaustion; return all items in served order."""

    items: list[dict] = []
    params: dict = {"limit": limit}
    if author is not None:
        params["author"] = author
    for _ in range(1000):  # hard stop: a cursor loop must not hang the suite
        page = (await client.get(BASE, params=params)).json()
        items.extend(page["items"])
        if page["next_cursor"] is None:
            return items
        params["cursor"] = page["next_cursor"]
    raise AssertionError("cursor chain did not terminate")


async def test_default_page_size_is_20(client, user_factory, article_factory):
    author = await user_factory()
    for i in range(25):
        await article_factory(author=author, created_at=T0 + timedelta(seconds=i))

    resp = await client.get(BASE)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 20
    assert data["next_cursor"] is not None


async def test_limit_is_respected(client, user_factory, article_factory):
    author = await user_factory()
    for i in range(5):
        await article_factory(author=author, created_at=T0 + timedelta(seconds=i))

    data = (await client.get(BASE, params={"limit": 3})).json()
    assert len(data["items"]) == 3


@pytest.mark.parametrize("bad_limit", [0, -1, 101, "abc"])
async def test_limit_out_of_bounds_is_422(client, bad_limit):
    resp = await client.get(BASE, params={"limit": bad_limit})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_order_is_newest_first(client, user_factory, article_factory):
    author = await user_factory()
    for i in range(4):
        await article_factory(
            author=author, title=f"article-{i}", created_at=T0 + timedelta(seconds=i)
        )

    items = (await client.get(BASE)).json()["items"]

    assert [item["title"] for item in items] == [f"article-{i}" for i in (3, 2, 1, 0)]


async def test_full_walk_has_no_duplicates_or_skips(client, user_factory, article_factory):
    author = await user_factory()
    created_ids = set()
    for i in range(45):
        article = await article_factory(author=author, created_at=T0 + timedelta(seconds=i))
        created_ids.add(article.id)

    items = await _walk(client, limit=10)
    walked_ids = [item["id"] for item in items]
    assert len(walked_ids) == len(set(walked_ids)) == 45  # no duplicates, no skips
    assert set(walked_ids) == created_ids
    # Deterministic order: newest-first across the whole chain, not just within pages.
    keys = [(item["created_at"], item["id"]) for item in items]
    assert keys == sorted(keys, reverse=True)


async def test_created_at_ties_across_page_boundary(client, user_factory, article_factory):
    """Five articles share one created_at; page size 2 forces boundaries inside the tie,
    the id tiebreaker must keep the chain exact."""

    author = await user_factory()
    tie_ids = {(await article_factory(author=author, created_at=T0)).id for _ in range(5)}
    items = await _walk(client, limit=2)
    walked_ids = [item["id"] for item in items]
    assert len(walked_ids) == len(set(walked_ids)) == 5
    assert set(walked_ids) == tie_ids
    assert walked_ids == sorted(walked_ids, reverse=True)  # id desc within the tie


async def test_soft_deleted_articles_are_excluded(client, user_factory, article_factory):
    author = await user_factory()
    live = await article_factory(author=author, created_at=T0)
    await article_factory(
        author=author, created_at=T0 + timedelta(seconds=1), deleted_at=datetime(2026, 7, 2)
    )
    items = (await client.get(BASE)).json()["items"]
    assert [item["id"] for item in items] == [live.id]


async def test_author_filter_returns_only_that_author(client, user_factory, article_factory):
    alice, bob = await user_factory(), await user_factory()
    alice_article = await article_factory(author=alice, created_at=T0)
    await article_factory(author=bob, created_at=T0 + timedelta(seconds=1))

    data = (await client.get(BASE, params={"author": alice.id})).json()
    assert [item["id"] for item in data["items"]] == [alice_article.id]
    assert all(item["author_id"] == alice.id for item in data["items"])


async def test_author_filter_composes_with_cursor(client, user_factory, article_factory):
    alice, bob = await user_factory(), await user_factory()
    alice_ids = set()
    for i in range(7):
        mine = await article_factory(author=alice, created_at=T0 + timedelta(seconds=i))
        alice_ids.add(mine.id)
        await article_factory(author=bob, created_at=T0 + timedelta(seconds=i, microseconds=1))

    items = await _walk(client, limit=3, author=alice.id)

    assert {item["id"] for item in items} == alice_ids
    assert all(item["author_id"] == alice.id for item in items)


async def test_unknown_author_returns_empty_list(client, user_factory, article_factory):
    await article_factory(created_at=T0)

    data = (await client.get(BASE, params={"author": 999999})).json()

    assert data["items"] == []
    assert data["next_cursor"] is None


async def test_terminal_page_has_null_cursor(client, user_factory, article_factory):
    author = await user_factory()
    for i in range(3):
        await article_factory(author=author, created_at=T0 + timedelta(seconds=i))

    data = (await client.get(BASE, params={"limit": 3})).json()

    assert len(data["items"]) == 3
    assert data["next_cursor"] is None  # exactly one full page: no phantom next page


@pytest.mark.parametrize("bad_cursor", ["garbage !!", "aGVsbG8", "///", "2026-07-01T12:00:00"])
async def test_invalid_cursor_is_422(client, bad_cursor):
    resp = await client.get(BASE, params={"cursor": bad_cursor})

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["request_id"]


async def test_empty_database_returns_empty_page(client):
    data = (await client.get(BASE)).json()
    assert data == {"items": [], "next_cursor": None}
