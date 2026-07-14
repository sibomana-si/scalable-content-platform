"""Smoke test: proves the ASGI + httpx harness works end-to-end.

Liveness has no dependencies, so this needs no containers. It is not a TDD red-green test -
it validates the plumbing that the FR acceptance tests will ride on.
"""

from httpx import AsyncClient


async def test_live(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}
