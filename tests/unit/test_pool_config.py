"""Unit tests for the connection-pool settings. No connections opened.

Every bound here exists to make a failure bounded. An unbounded pool wait turns one slow
query into a total stall, an unbounded overflow turns a traffic spike into a MySQL connection
refusal for every replica at once, and a connection kept past a proxy's idle timeout comes
back as "server has gone away" on a request that did nothing wrong.
"""

import pytest

from app.cache.client import build_redis_kwargs
from app.config import Settings
from app.db.session import build_engine_kwargs


@pytest.fixture
def settings() -> Settings:
    return Settings()


# --- MySQL ---------------------------------------------------------------------------------


def test_engine_kwargs_come_from_settings(settings):
    settings.db_pool_size = 7
    settings.db_max_overflow = 3
    settings.db_pool_timeout = 4.5
    settings.db_pool_recycle = 900

    kwargs = build_engine_kwargs(settings)

    assert kwargs["pool_size"] == 7
    assert kwargs["max_overflow"] == 3
    assert kwargs["pool_timeout"] == 4.5
    assert kwargs["pool_recycle"] == 900


def test_the_pool_pre_pings(settings):
    """A connection the server closed underneath the pool must fail on checkout, not mid-query."""

    assert build_engine_kwargs(settings)["pool_pre_ping"] is True


def test_every_pool_default_is_bounded(settings):
    """SQLAlchemy's defaults are generous. A ceiling that is never reached is still a ceiling."""

    kwargs = build_engine_kwargs(settings)

    assert 0 < kwargs["pool_size"] < 100
    assert 0 <= kwargs["max_overflow"] < 100
    assert 0 < kwargs["pool_timeout"] < 60
    assert 0 < kwargs["pool_recycle"] < 28_800  # under MySQL's default wait_timeout


def test_the_pool_timeout_is_never_unbounded(settings):
    """`pool_timeout=None` waits forever. A request that cannot get a connection must fail fast."""

    settings.db_pool_timeout = 0

    with pytest.raises(ValueError, match="DB_POOL_TIMEOUT"):
        build_engine_kwargs(settings)


def test_a_negative_overflow_is_rejected(settings):
    """SQLAlchemy reads -1 as unlimited overflow, which removes the ceiling entirely."""

    settings.db_max_overflow = -1

    with pytest.raises(ValueError, match="DB_MAX_OVERFLOW"):
        build_engine_kwargs(settings)


def test_the_recycle_stays_under_the_mysql_idle_timeout(settings):
    """Recycling after wait_timeout is recycling too late: the server already dropped it."""

    assert build_engine_kwargs(settings)["pool_recycle"] < 28_800


# --- Redis ----------------------------------------------------------------------------------


def test_redis_kwargs_bound_the_pool(settings):
    settings.redis_max_connections = 25

    assert build_redis_kwargs(settings)["max_connections"] == 25


def test_the_redis_pool_default_is_bounded(settings):
    """redis-py grows its pool without limit; a stalled server would then open sockets forever."""

    kwargs = build_redis_kwargs(settings)

    assert 0 < kwargs["max_connections"] < 1000


def test_the_redis_socket_timeouts_stay_bounded(settings):
    kwargs = build_redis_kwargs(settings)

    assert 0 < kwargs["socket_timeout"] <= 5
    assert 0 < kwargs["socket_connect_timeout"] <= 5


def test_a_non_positive_redis_pool_is_rejected(settings):
    settings.redis_max_connections = 0

    with pytest.raises(ValueError, match="REDIS_MAX_CONNECTIONS"):
        build_redis_kwargs(settings)
