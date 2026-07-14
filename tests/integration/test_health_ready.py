"""Integration smoke test: the walking skeleton threaded through MySQL + Redis.

Requires the container stack (``docker compose up`` locally; service containers in CI).
Skips cleanly when the dependencies are absent.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_ready(client: AsyncClient, db_available: None, redis_available: None) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"mysql": "ok", "redis": "ok"}
