"""A read that fails mid-request must still be retryable, and must still answer 503.

Chaos experiment 4 found the defect this file pins. Under a MySQL ``reset_peer`` the service
answered **500** and the circuit breaker never opened, while the same dependency under
``blackhole`` answered 503 correctly. The difference is the retry: a blackholed host expires the
call timeout before a second attempt exists, and a refused connection fails fast enough to reach
one.

The second attempt was the problem. ``with_retry`` rolls the session back between attempts, the
request transaction lived inside an ``async with session.begin()`` block, and a rollback closes
that block. Every statement after it raised ``InvalidRequestError`` — an error the retry layer
does not call transient, so nothing translated it, the guard read it as an answer from a working
dependency and recorded a breaker **success**, and the API answered 500.

Two tests, at the two levels the bug crossed:

- the session dependency must survive a rollback and serve another statement;
- a repository read whose connection dies mid-transaction must raise
  ``DependencyUnavailableError`` and count against the breaker.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.session import get_session
from app.repositories.article_repo import ArticleRepository
from app.resilience.breaker import _BREAKERS, BreakerState, CircuitBreaker
from app.services.exceptions import DependencyUnavailableError
from scripts.inject_fault import Toxiproxy, toxic_for
from tests.conftest import TOXIPROXY_MYSQL_PORT, TOXIPROXY_URL

pytestmark = pytest.mark.integration


async def test_the_request_session_survives_a_rollback(db_available: None) -> None:
    """Roll back mid-request, then read again.

    This is what ``with_retry`` does between attempts. If the session cannot serve a statement
    afterwards, the retry proves only that the session is broken — which is the failure the
    docstring of ``with_retry`` says the rollback exists to prevent.
    """
    dependency = get_session()
    session = await anext(dependency)
    try:
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        await session.rollback()
        assert (await session.execute(text("SELECT 2"))).scalar_one() == 2
    finally:
        with pytest.raises(StopAsyncIteration):
            await anext(dependency)


@pytest.mark.chaos
async def test_a_connection_lost_mid_read_answers_unavailable(
    db_available: None, chaos_available: None
) -> None:
    """The experiment-4 case: a live connection is cut, and the read must refuse, not crash."""
    settings = get_settings()
    url = settings.async_database_url.replace(
        f":{settings.mysql_port}/", f":{TOXIPROXY_MYSQL_PORT}/"
    )
    # No pre-ping: the point is a connection that dies after checkout, not one caught before it.
    engine = create_async_engine(url, pool_pre_ping=False)
    injector = Toxiproxy(TOXIPROXY_URL)
    injector.clear("mysql")
    # A threshold of one, so a single lost connection makes the verdict visible. The autouse
    # `_reset_breakers` fixture clears the registry again after the test.
    breaker = CircuitBreaker(
        "mysql", failure_threshold=1, reset_seconds=10.0, half_open_max_calls=1
    )
    _BREAKERS["mysql"] = breaker
    try:
        async with async_sessionmaker(bind=engine)() as session:
            # Check a connection out and open the transaction before the fault lands.
            await session.execute(text("SELECT 1"))
            injector.apply("mysql", toxic_for("reset_peer"))
            with pytest.raises(DependencyUnavailableError):
                await ArticleRepository(session).get(1)
        assert breaker.state is BreakerState.OPEN, (
            "a dead connection must count against the breaker, not as an answer from it"
        )
    finally:
        injector.clear("mysql")
        await engine.dispose()
