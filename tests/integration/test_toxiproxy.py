"""The fault injector, proved against a real dependency before any experiment trusts it.

`tests/unit/test_fault_injector.py` proves the payloads. This proves the harness: a latency toxic
put on the MySQL proxy really does delay a query, and removing it really does give the connection
back. Without this, a chaos experiment that measured nothing would look exactly like a system that
degraded gracefully.

The assertions bound the delay from below and the recovery from above, in the margin style of
`tests/integration/test_pool_exhaustion.py`. Nothing here asserts that a latency was small, so a
core that ramped late cannot fail the test.
"""

import time

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import get_settings
from scripts.inject_fault import Toxiproxy, toxic_for
from tests.conftest import TOXIPROXY_MYSQL_PORT, TOXIPROXY_URL

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

# Chosen well above the round-trip cost of a local `SELECT 1`, so the assertion cannot pass on
# ordinary jitter, and well under the wall-clock bound below.
LATENCY_MS = 800


@pytest.fixture
def injector(chaos_available: None) -> Toxiproxy:  # type: ignore[misc]
    client = Toxiproxy(TOXIPROXY_URL)
    client.clear("mysql")
    yield client
    client.clear("mysql")


@pytest_asyncio.fixture
async def proxied_connection(db_available: None) -> AsyncConnection:  # type: ignore[misc]
    """One MySQL connection through the proxy, opened before any toxic is injected.

    Opening it first keeps the MySQL handshake out of the measurement: a delay applied to a
    multi-round-trip handshake times out long before it says anything about a query.
    """

    settings = get_settings()
    url = settings.async_database_url.replace(
        f":{settings.mysql_port}/", f":{TOXIPROXY_MYSQL_PORT}/"
    )
    engine = create_async_engine(url, pool_pre_ping=False)
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
        yield connection
    await engine.dispose()


async def _timed_select(connection: AsyncConnection) -> float:
    start = time.monotonic()
    await connection.execute(text("SELECT 1"))
    return time.monotonic() - start


async def test_a_latency_toxic_delays_a_query_and_clearing_it_gives_the_time_back(
    injector: Toxiproxy, proxied_connection: AsyncConnection
) -> None:
    baseline = await _timed_select(proxied_connection)
    assert baseline < 1.0, "the proxy hop alone should cost nothing like a second"

    injector.apply("mysql", toxic_for("latency", ms=LATENCY_MS))
    delayed = await _timed_select(proxied_connection)

    injector.clear("mysql")
    recovered = await _timed_select(proxied_connection)

    # Bound the injected delay from below only. How much more than 800 ms it costs depends on
    # how many packets the round trip takes, which is not this test's business.
    assert delayed >= LATENCY_MS / 1000 * 0.9, (
        f"the latency toxic did not reach the query: {delayed:.3f}s"
    )
    assert recovered < 1.0, f"clearing the toxic did not restore the path: {recovered:.3f}s"


async def test_the_injector_reports_what_it_holds(injector: Toxiproxy) -> None:
    assert injector.active("mysql") == []

    injector.apply("mysql", toxic_for("latency", ms=LATENCY_MS))

    active = injector.active("mysql")
    assert [toxic["name"] for toxic in active] == ["latency"]
    assert active[0]["attributes"]["latency"] == LATENCY_MS


async def test_clearing_one_target_leaves_the_other_alone(injector: Toxiproxy) -> None:
    """Experiment 2 holds a fault on Redis while MySQL stays healthy."""

    redis_injector = Toxiproxy(TOXIPROXY_URL)
    redis_injector.clear("redis")
    try:
        redis_injector.apply("redis", toxic_for("latency", ms=100))
        injector.apply("mysql", toxic_for("latency", ms=100))

        injector.clear("mysql")

        assert [toxic["name"] for toxic in redis_injector.active("redis")] == ["latency"]
    finally:
        redis_injector.clear("redis")
