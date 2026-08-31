"""The two timeouts the engine carries into every connection it opens.

A pool bound (`tests/unit/test_pool_config.py`) decides how long a request waits for a
connection. These decide how long it waits once it has one. Both ends need a ceiling: a TCP
connect to a blackholed host waits for the kernel, and a runaway `SELECT` waits for MySQL.
"""

import pytest

from app.config import Settings
from app.db.session import build_engine_kwargs


@pytest.fixture
def settings() -> Settings:
    return Settings()


def test_the_engine_carries_connect_arguments(settings) -> None:
    assert "connect_args" in build_engine_kwargs(settings)


def test_the_connect_timeout_is_set_and_bounded(settings) -> None:
    """aiomysql defaults to no connect timeout, so a blackholed host holds the socket open.

    The default matters more than the knob: most deployments never set one.
    """

    connect_args = build_engine_kwargs(settings)["connect_args"]

    assert connect_args["connect_timeout"] is not None
    assert 0 < connect_args["connect_timeout"] <= 10


def test_the_connect_timeout_comes_from_settings(settings) -> None:
    settings.db_connect_timeout_seconds = 4.0

    assert build_engine_kwargs(settings)["connect_args"]["connect_timeout"] == 4.0


def test_every_session_sets_a_statement_timeout(settings) -> None:
    """The server-side half. A client that gives up alone leaves the query running.

    `max_execution_time` makes MySQL kill the statement, so the connection comes back to the
    pool instead of staying pinned to work nobody is waiting for.
    """

    settings.db_statement_timeout_seconds = 2.5

    init_command = build_engine_kwargs(settings)["connect_args"]["init_command"]

    assert "max_execution_time" in init_command
    assert "2500" in init_command


def test_the_statement_timeout_is_expressed_in_milliseconds(settings) -> None:
    """`max_execution_time` takes milliseconds. Passing seconds makes a 2 s bound a 2 ms bound."""

    settings.db_statement_timeout_seconds = 1.0

    assert "1000" in build_engine_kwargs(settings)["connect_args"]["init_command"]


def test_the_statement_timeout_default_is_bounded(settings) -> None:
    assert 0 < settings.db_statement_timeout_seconds <= 10


def test_a_non_positive_connect_timeout_is_rejected(settings) -> None:
    """aiomysql reads 0 as "wait forever", which is the ceiling this removes."""

    settings.db_connect_timeout_seconds = 0

    with pytest.raises(ValueError, match="DB_CONNECT_TIMEOUT_SECONDS"):
        build_engine_kwargs(settings)


def test_a_non_positive_statement_timeout_is_rejected(settings) -> None:
    """MySQL reads `max_execution_time=0` as no limit."""

    settings.db_statement_timeout_seconds = 0

    with pytest.raises(ValueError, match="DB_STATEMENT_TIMEOUT_SECONDS"):
        build_engine_kwargs(settings)


def test_the_pool_bounds_still_hold(settings) -> None:
    """The new keys must not displace the old ones."""

    kwargs = build_engine_kwargs(settings)

    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_timeout"] > 0
