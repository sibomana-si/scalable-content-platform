"""Pool exhaustion must fail, and fail fast, against a real MySQL.

The requirement is a bounded failure, not success. A pool that hands out connections
without limit takes MySQL down for every replica at once, and a pool that waits without limit
turns one slow query into a total stall: every later request queues behind it and nothing ever
fails, so nothing ever alerts.
"""

import asyncio
import time

import pytest
from sqlalchemy.exc import TimeoutError as SQLTimeoutError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db.session import build_engine_kwargs

pytestmark = pytest.mark.integration

POOL_SIZE = 2
MAX_OVERFLOW = 1
POOL_TIMEOUT = 1.0


@pytest.fixture
async def small_engine(db_available: None):
    """A deliberately tiny pool, so exhaustion takes three connections rather than fifteen."""

    settings = get_settings()
    kwargs = build_engine_kwargs(settings)
    kwargs.update(pool_size=POOL_SIZE, max_overflow=MAX_OVERFLOW, pool_timeout=POOL_TIMEOUT)
    engine = create_async_engine(settings.async_database_url, **kwargs)
    yield engine
    await engine.dispose()


async def test_the_pool_hands_out_size_plus_overflow_connections(small_engine):
    """Overflow is real capacity, not a formality: the pool must actually lend it out."""

    connections = [await small_engine.connect() for _ in range(POOL_SIZE + MAX_OVERFLOW)]
    try:
        assert len(connections) == 3
        assert small_engine.pool.checkedout() == 3
    finally:
        for connection in connections:
            await connection.close()


async def test_one_connection_past_the_limit_raises_inside_the_timeout(small_engine):
    """Bounded failure. The caller gets an error it can turn into a 503, not an open wait."""

    connections = [await small_engine.connect() for _ in range(POOL_SIZE + MAX_OVERFLOW)]
    try:
        started = time.monotonic()
        with pytest.raises(SQLTimeoutError):
            await small_engine.connect()
        elapsed = time.monotonic() - started

        assert elapsed < POOL_TIMEOUT + 1.0
    finally:
        for connection in connections:
            await connection.close()


async def test_a_returned_connection_unblocks_a_waiter(small_engine):
    """The ceiling is a queue, not a wall: capacity comes back when a request finishes."""
    connections = [await small_engine.connect() for _ in range(POOL_SIZE + MAX_OVERFLOW)]

    async def release_soon() -> None:
        await asyncio.sleep(0.1)
        await connections.pop().close()

    releaser = asyncio.create_task(release_soon())
    try:
        waiter = await small_engine.connect()
        await waiter.close()
    finally:
        await releaser
        for connection in connections:
            await connection.close()


async def test_concurrent_demand_past_the_limit_fails_rather_than_hangs(small_engine):
    """Ten callers, three slots, and each holder outlasts the pool timeout.

    The losers must raise, and quickly. The held query is deliberately longer than
    ``pool_timeout`` — a shorter one drains the queue before the ceiling can bite, which
    would make this test pass for the wrong reason.
    """

    async def hold() -> str:
        try:
            async with small_engine.connect() as connection:
                await connection.exec_driver_sql(f"SELECT SLEEP({POOL_TIMEOUT * 1.5})")
                return "ok"
        except SQLTimeoutError:
            return "timeout"

    started = time.monotonic()
    results = await asyncio.gather(*(hold() for _ in range(10)))
    elapsed = time.monotonic() - started

    assert "timeout" in results  # the ceiling held
    assert "ok" in results  # and it was not a total outage
    assert elapsed < 10.0  # nobody waited without a bound


async def test_the_pool_gauge_reports_checked_out_connections(small_engine):
    """The number an operator needs during an exhaustion incident."""

    from prometheus_client import CollectorRegistry

    from app.observability.metrics import register_pool_metrics

    registry = CollectorRegistry()
    register_pool_metrics(registry, lambda: small_engine)

    async with small_engine.connect():
        in_use = registry.get_sample_value("db_pool_connections", {"state": "in_use"})

    assert in_use == 1.0
