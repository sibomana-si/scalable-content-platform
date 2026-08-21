"""Smoke test: proves the ASGI + httpx harness works end-to-end.

Liveness has no dependencies, so this needs no containers. It is not a TDD red-green test;
it validates the plumbing that the FR acceptance tests will ride on.
"""

from httpx import AsyncClient


async def test_live(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


async def test_live_reports_a_stable_instance_id(client: AsyncClient) -> None:
    """The multi-replica test counts distinct instances through a load balancer.

    The id must be stable for the life of the process — a fresh value per request would make
    every replica look like many, and the test would pass without ever proving scale-out.
    """

    first = (await client.get("/health/live")).json()["instance"]
    second = (await client.get("/health/live")).json()["instance"]

    assert first == second
    assert first


async def test_the_instance_id_appears_on_no_other_endpoint(client: AsyncClient) -> None:
    """Infrastructure detail belongs on the probe, not on the data path."""

    response = await client.get("/v1/articles/1")

    assert "instance" not in response.text
