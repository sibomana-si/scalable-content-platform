"""Integration tests for readiness under a partial outage.

Readiness answers one question: should this instance keep receiving traffic? It is not a
health report, and it is not the sum of the dependency checks. ADR-0004 makes the cache an
optimization, so an instance with a dead cache still serves every read from MySQL, and a
readiness probe that fails on it removes an instance that works. Do that on every replica at
once, as a Redis outage does, and the cache failure becomes the outage.

MySQL is the other case. Nothing answers without it, so it decides the status code.
"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient

from app.api.routers import health
from app.cache.client import get_redis
from app.config import get_settings
from app.db.session import get_engine

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _fresh_clients() -> AsyncGenerator[None, None]:
    """Fresh Settings and fresh pooled clients around each test.

    Same reasoning as ``tests/integration/test_health_ready.py``: the clients are process-wide
    caches and every test gets its own event loop.
    """

    async def reset() -> None:
        await get_engine().dispose()
        get_redis.cache_clear()
        get_settings.cache_clear()

    await reset()
    yield
    await reset()


class _RefusingRedis:
    """Redis is gone. The command fails at once, the way a refused connection does."""

    async def ping(self) -> Any:
        raise ConnectionError("connection refused")


async def test_a_dead_cache_leaves_the_instance_ready(
    app: FastAPI, client: AsyncClient, db_available: None
) -> None:
    app.dependency_overrides[get_redis] = lambda: _RefusingRedis()

    resp = await client.get("/health/ready")

    # 200, so the load balancer keeps sending traffic this instance can still serve.
    assert resp.status_code == 200
    body = resp.json()
    # And `degraded`, so an operator reading the probe still learns the cache is down.
    assert body["status"] == "degraded"
    assert body["checks"]["mysql"] == "ok"
    assert body["checks"]["redis"].startswith("error:")


async def test_a_dead_database_makes_the_instance_unready(
    app: FastAPI, client: AsyncClient, db_available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fails() -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr(health, "_probe_mysql", _fails)

    resp = await client.get("/health/ready")

    # Nothing answers without MySQL, so this instance must leave the pool.
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unready"
    assert body["checks"]["mysql"].startswith("error:")


async def test_the_probe_still_reports_every_dependency(
    app: FastAPI, client: AsyncClient, db_available: None
) -> None:
    app.dependency_overrides[get_redis] = lambda: _RefusingRedis()

    checks = (await client.get("/health/ready")).json()["checks"]

    # The status code narrowed; the report did not. Losing the Redis line would hide the
    # outage that the 200 now deliberately tolerates.
    assert set(checks) == {"mysql", "redis"}
