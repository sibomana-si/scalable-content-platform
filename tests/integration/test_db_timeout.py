"""The statement timeout, against a real MySQL.

The unit test proves the engine carries `max_execution_time`. Only the server can prove it acts
on it. The second assertion matters as much as the first: a bounded failure that leaks the
connection turns one slow query into a pool leak, which is worse than the query.
"""

import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db.session import build_engine_kwargs

pytestmark = pytest.mark.integration

STATEMENT_TIMEOUT = 1.0

# A cartesian product over `information_schema.columns`. Two properties make it the right probe:
# it runs for minutes, and it needs no seeded row, so the test does not depend on what ran
# before it.
#
# Do not probe with `SELECT SLEEP(30)`. MySQL interrupts it at `max_execution_time` and then
# returns 1 rather than an error, so a run against a working timeout looks like a run against
# no timeout at all.
RUNAWAY_QUERY = "SELECT COUNT(*) FROM information_schema.columns a, information_schema.columns b"


@pytest.fixture
async def bounded_engine(db_available: None):
    # A private Settings, not the cached one: mutating the process settings would carry the
    # tight timeout into every test that runs after this file.
    settings = Settings()
    settings.db_statement_timeout_seconds = STATEMENT_TIMEOUT
    kwargs = build_engine_kwargs(settings)
    kwargs.update(pool_size=2, max_overflow=0)
    engine = create_async_engine(settings.async_database_url, **kwargs)
    yield engine
    await engine.dispose()


async def test_a_runaway_select_is_killed_inside_the_timeout(bounded_engine) -> None:
    started = time.monotonic()

    with pytest.raises((OperationalError, DBAPIError)):
        async with bounded_engine.connect() as connection:
            await connection.execute(text(RUNAWAY_QUERY))

    elapsed = time.monotonic() - started

    assert elapsed < STATEMENT_TIMEOUT + 1.0


async def test_the_server_names_the_reason(bounded_engine) -> None:
    """Error 3024. An operator reading the log must not have to guess which timeout fired."""

    with pytest.raises((OperationalError, DBAPIError)) as raised:
        async with bounded_engine.connect() as connection:
            await connection.execute(text(RUNAWAY_QUERY))

    assert "3024" in str(raised.value)


async def test_the_killed_connection_returns_to_the_pool(bounded_engine) -> None:
    """Otherwise the pool empties one runaway query at a time, and the next request waits."""

    with pytest.raises((OperationalError, DBAPIError)):
        async with bounded_engine.connect() as connection:
            await connection.execute(text(RUNAWAY_QUERY))

    assert bounded_engine.pool.checkedout() == 0


async def test_a_quick_query_is_untouched(bounded_engine) -> None:
    """The bound must not be so tight that ordinary work trips it."""

    async with bounded_engine.connect() as connection:
        assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
