"""Integration smoke test: the walking skeleton threaded through MySQL + Redis.

Requires the container stack (``docker compose up`` locally; service containers in CI).
Skips cleanly when the dependencies are absent.

Beyond the happy path, readiness carries a resilience obligation: it must not be able to take
the service down. A probe that pins a request-pool connection while waiting on a second,
unbounded dependency turns a degraded cache into an exhausted database pool — so the checks
below assert the shape of the probe, not just its verdict.
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient

from app.cache.client import get_redis
from app.config import get_settings
from app.db.session import get_engine

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _fresh_clients() -> AsyncGenerator[None, None]:
    """Fresh Settings and fresh pooled clients around each test.

    ``get_engine``/``get_redis`` are process-wide ``lru_cache``s while every test gets its own
    event loop, so a pooled aiomysql connection opened under a closed loop raises "Event loop
    is closed" when the next test reuses it — the same reason ``clean_db`` disposes the engine.
    Readiness also reads its timeout from ``Settings`` at call time.
    """

    async def reset() -> None:
        await get_engine().dispose()
        get_redis.cache_clear()
        get_settings.cache_clear()

    await reset()
    yield
    await reset()


class _SpyRedis:
    """Records how many DB connections are checked out at the moment Redis is probed."""

    def __init__(self) -> None:
        self.checked_out_during_ping: int | None = None

    async def ping(self) -> bool:
        self.checked_out_during_ping = get_engine().pool.checkedout()  # type: ignore[attr-defined]
        return True


class _HungRedis:
    """Reachable, accepts the command, never answers — a blackholed cache."""

    async def ping(self) -> Any:
        await asyncio.sleep(30)


async def test_ready(client: AsyncClient, db_available: None, redis_available: None) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"mysql": "ok", "redis": "ok"}


async def test_ready_holds_no_db_connection_while_it_probes_redis(
    app: FastAPI, client: AsyncClient, db_available: None
) -> None:
    # The failure this pins: the DB connection outliving its own SELECT 1. Held across an
    # unbounded ping, every orphaned probe pins one until the pool (10 + 10 overflow) is gone
    # and real traffic blocks for pool_timeout.
    spy = _SpyRedis()
    app.dependency_overrides[get_redis] = lambda: spy

    resp = await client.get("/health/ready")

    assert resp.status_code == 200
    assert spy.checked_out_during_ping == 0


async def test_ready_gives_up_on_a_hanging_dependency(
    app: FastAPI, client: AsyncClient, db_available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "0.2")
    get_settings.cache_clear()
    app.dependency_overrides[get_redis] = lambda: _HungRedis()

    started = time.perf_counter()
    resp = await client.get("/health/ready")
    elapsed = time.perf_counter() - started

    # 200, because a dead cache is not a reason to remove an instance that still serves every
    # read from MySQL — see tests/integration/test_health_ready_degraded.py. What this test
    # pins is the timeout underneath that verdict.
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["mysql"] == "ok"
    assert body["checks"]["redis"] == "error: TimeoutError"
    # Bounded is the whole point: a probe that never returns is what accumulates.
    assert elapsed < 5, f"probe took {elapsed:.1f}s; it should give up at the configured timeout"
    assert get_engine().pool.checkedout() == 0  # type: ignore[attr-defined]
