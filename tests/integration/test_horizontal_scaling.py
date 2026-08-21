"""Statelessness under scale-out: two app instances, one MySQL, one Redis.

This deployment test runs in CI on every push, and it catches the following failures:
a per-process cache, an in-memory session store, or a counter that only one instance sees.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app

pytestmark = pytest.mark.integration

BASE = "/v1/articles"
PAYLOAD = {"title": "Written on one instance", "body": "Read from another."}


@pytest_asyncio.fixture
async def instances(db_available: None, redis_available: None):
    """Two independent apps with their own clients, sharing the containers.

    Separate ``create_app()`` calls, so each has its own middleware chain, its own routers,
    and its own dependency cache — everything a second replica would have.
    """

    app_a, app_b = create_app(), create_app()
    async with (
        AsyncClient(transport=ASGITransport(app=app_a), base_url="http://a") as a,
        AsyncClient(transport=ASGITransport(app=app_b), base_url="http://b") as b,
    ):
        yield a, b


async def test_a_write_on_one_instance_is_readable_on_the_other(
    instances, clean_db: None, clean_cache: None, user_factory, auth_headers
):
    a, b = instances
    user = await user_factory()

    created = await a.post(BASE, json=PAYLOAD, headers=auth_headers(user))
    article_id = created.json()["id"]

    read = await b.get(f"{BASE}/{article_id}")
    assert read.status_code == 200
    assert read.json() == created.json()


async def test_the_cache_is_shared_not_per_process(
    instances, clean_db: None, clean_cache: None, article_factory
):
    """A per-process cache would make this a miss on B. The shared one makes it a hit."""

    a, b = instances
    article = await article_factory()

    await a.get(f"{BASE}/{article.id}")  # populate through A
    before = await hits(b, "article")
    await b.get(f"{BASE}/{article.id}")  # must hit through B

    assert await hits(b, "article") == before + 1


async def test_an_update_on_one_instance_is_not_stale_on_the_other(
    instances, clean_db: None, clean_cache: None, user_factory, article_factory, auth_headers
):
    """Invalidation must cross the process boundary. A per-process cache fails here."""

    a, b = instances
    user = await user_factory()
    article = await article_factory(author=user)
    await b.get(f"{BASE}/{article.id}")  # B caches the old body

    await a.put(
        f"{BASE}/{article.id}",
        json=PAYLOAD,
        headers={**auth_headers(user), "If-Match": article.updated_at.isoformat()},
    )

    assert (await b.get(f"{BASE}/{article.id}")).json()["title"] == PAYLOAD["title"]


async def test_a_delete_on_one_instance_is_a_404_on_the_other(
    instances, clean_db: None, clean_cache: None, user_factory, article_factory, auth_headers
):
    a, b = instances
    user = await user_factory()
    article = await article_factory(author=user)
    await b.get(f"{BASE}/{article.id}")

    await a.delete(
        f"{BASE}/{article.id}",
        headers={**auth_headers(user), "If-Match": article.updated_at.isoformat()},
    )

    assert (await b.get(f"{BASE}/{article.id}")).status_code == 404


async def test_a_token_minted_on_one_instance_is_accepted_by_the_other(
    instances, clean_db: None, clean_cache: None
):
    """No server-side session, so no sticky routing and no shared session store.

    The token is minted by A's own login endpoint rather than by the test fixture, so this
    covers the whole path: A signs it, B verifies it, and neither shares memory with the other.
    """

    a, b = instances
    credentials = {"email": "scaleout@example.com", "password": "a-long-unique-passphrase-42"}
    assert (await a.post("/v1/auth/register", json=credentials)).status_code == 201
    token = (await a.post("/v1/auth/login", json=credentials)).json()["access_token"]

    created = await b.post(BASE, json=PAYLOAD, headers={"Authorization": f"Bearer {token}"})

    assert created.status_code == 201


async def test_an_alternating_request_sequence_matches_running_it_on_one_instance(
    instances, clean_db: None, clean_cache: None, user_factory, article_factory, auth_headers
):
    """No instance affinity: where each request lands must not change the outcome."""

    a, b = instances
    user = await user_factory()
    article = await article_factory(author=user)

    alternating = [
        (await a.get(f"{BASE}/{article.id}")).json(),
        (await b.get(f"{BASE}/{article.id}")).json(),
        (await a.get(BASE)).json(),
        (await b.get(BASE)).json(),
    ]

    assert alternating[0] == alternating[1]
    assert alternating[2] == alternating[3]


async def test_a_cursor_issued_by_one_instance_is_honored_by_the_other(
    instances, clean_db: None, clean_cache: None, user_factory, article_factory
):
    """The cursor is opaque, so it must not encode anything instance-local."""

    a, b = instances
    user = await user_factory()
    for _ in range(3):
        await article_factory(author=user)

    first = await a.get(BASE, params={"limit": 2})
    cursor = first.json()["next_cursor"]
    second = await b.get(BASE, params={"limit": 2, "cursor": cursor})

    assert second.status_code == 200
    assert second.json()["items"] != first.json()["items"]


async def test_each_instance_exports_its_own_request_counters(
    instances, clean_db: None, clean_cache: None
):
    """Metrics are per-process by design.

    Prometheus scrapes each replica separately and sums in PromQL, so a shared counter would
    be the bug. The in-process harness shares one default registry, so this asserts the
    weaker thing it can: both instances export the series at all.
    """

    a, b = instances

    for client in (a, b):
        assert "http_requests_total" in (await client.get("/metrics")).text


async def hits(client: AsyncClient, entity: str) -> float:
    """One cache hit counter from a client's own /metrics."""

    needle = f'cache_hits_total{{entity="{entity}"}} '
    for line in (await client.get("/metrics")).text.splitlines():
        if line.startswith(needle):
            return float(line[len(needle) :])
    return 0.0
