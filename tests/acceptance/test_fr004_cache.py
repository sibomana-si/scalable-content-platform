"""FR-004 acceptance: the cache-aside read path, exercised over HTTP.

These tests read ``/metrics`` rather than Redis. A green unit test proves the code calls the
cache; only the exported counter proves the deployed read path did, which is the number the
capacity model and the M6 load test both depend on.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

BASE = "/v1/articles"
PAYLOAD = {"title": "A title", "body": "Some body text."}


async def counter(client: AsyncClient, name: str, entity: str) -> float:
    """One cache counter from the scrape endpoint, or 0.0 before its first increment."""
    exposition = (await client.get("/metrics")).text
    needle = f'{name}{{entity="{entity}"}} '
    for line in exposition.splitlines():
        if line.startswith(needle):
            return float(line[len(needle) :])
    return 0.0


# --- article detail ---------------------------------------------------------------------------


async def test_a_repeated_read_is_served_from_the_cache(client, article_factory):
    article = await article_factory()
    hits_before = await counter(client, "cache_hits_total", "article")
    misses_before = await counter(client, "cache_misses_total", "article")

    first = await client.get(f"{BASE}/{article.id}")
    second = await client.get(f"{BASE}/{article.id}")

    assert first.status_code == second.status_code == 200
    assert await counter(client, "cache_misses_total", "article") == misses_before + 1
    assert await counter(client, "cache_hits_total", "article") == hits_before + 1


async def test_the_cached_body_is_identical_to_the_database_body(client, article_factory):
    """A serialization round trip must not change one byte the client sees."""
    article = await article_factory()

    first = await client.get(f"{BASE}/{article.id}")
    second = await client.get(f"{BASE}/{article.id}")

    assert first.json() == second.json()


async def test_the_cached_updated_at_still_works_as_an_if_match_token(
    client, article_factory, auth_headers, user_factory
):
    """The CAS token survives the cache: microseconds must round-trip through JSON."""
    user = await user_factory()
    article = await article_factory(author=user)
    await client.get(f"{BASE}/{article.id}")  # populate
    cached = (await client.get(f"{BASE}/{article.id}")).json()

    response = await client.put(
        f"{BASE}/{article.id}",
        json=PAYLOAD,
        headers={**auth_headers(user), "If-Match": cached["updated_at"]},
    )

    assert response.status_code == 200


async def test_a_missing_article_is_still_a_404_on_the_second_read(client):
    """Nothing negative is cached, and a miss on a missing row must not become a 500."""
    assert (await client.get(f"{BASE}/999999")).status_code == 404
    assert (await client.get(f"{BASE}/999999")).status_code == 404


# --- list pages -----------------------------------------------------------------------------


async def test_a_repeated_list_read_is_served_from_the_cache(client, article_factory):
    await article_factory()
    misses_before = await counter(client, "cache_misses_total", "list")
    hits_before = await counter(client, "cache_hits_total", "list")

    first = await client.get(BASE)
    second = await client.get(BASE)

    assert first.json() == second.json()
    assert await counter(client, "cache_misses_total", "list") == misses_before + 1
    assert await counter(client, "cache_hits_total", "list") == hits_before + 1


async def test_a_cached_page_round_trips_its_next_cursor(client, user_factory, article_factory):
    """A page whose cursor was dropped on the way into the cache would end pagination early."""
    user = await user_factory()
    for _ in range(3):
        await article_factory(author=user)

    live = await client.get(BASE, params={"limit": 2})
    cached = await client.get(BASE, params={"limit": 2})

    assert live.json()["next_cursor"] is not None
    assert cached.json()["next_cursor"] == live.json()["next_cursor"]


async def test_a_cursored_page_is_cached_separately_from_the_first_page(
    client, user_factory, article_factory
):
    """Two windows sharing one key would serve page one's rows for page two."""
    user = await user_factory()
    for _ in range(3):
        await article_factory(author=user)
    first = await client.get(BASE, params={"limit": 2})
    cursor = first.json()["next_cursor"]

    second = await client.get(BASE, params={"limit": 2, "cursor": cursor})

    assert second.json()["items"] != first.json()["items"]


async def test_an_author_filtered_page_is_cached_separately_from_the_unfiltered_page(
    client, user_factory, article_factory
):
    author = await user_factory()
    other = await user_factory()
    await article_factory(author=author)
    await article_factory(author=other)

    unfiltered = await client.get(BASE)
    filtered = await client.get(BASE, params={"author": author.id})

    assert len(unfiltered.json()["items"]) == 2
    assert len(filtered.json()["items"]) == 1


# --- degradation -------------------------------------------------------------------------------


async def test_reads_succeed_with_the_cache_disabled(app, article_factory, monkeypatch):
    """CACHE_ENABLED=false must bypass Redis, not break the read path."""
    from httpx import ASGITransport

    from app.config import get_settings

    article = await article_factory()
    settings = get_settings()
    monkeypatch.setattr(settings, "cache_enabled", False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bare:
        response = await bare.get(f"{BASE}/{article.id}")

    assert response.status_code == 200
    assert response.json()["id"] == article.id
