"""The resilience settings, validated before anything opens a socket.

`tests/unit/test_pool_config.py` applied to the timeout budget. Every bound here makes a failure
bounded, and every unbounded form is refused by name: an env var that silently removes a ceiling
is worse than one that is missing, because the system still starts.
"""

import pytest

from app.config import Settings
from app.resilience.policy import DependencyPolicy, build_policies


@pytest.fixture
def settings() -> Settings:
    return Settings()


def test_a_policy_is_built_for_every_dependency_the_request_path_touches(settings) -> None:
    policies = build_policies(settings)

    assert set(policies) == {"mysql", "redis"}
    assert all(isinstance(policy, DependencyPolicy) for policy in policies.values())


def test_the_database_policy_comes_from_settings(settings) -> None:
    settings.db_call_timeout_seconds = 3.5
    settings.db_statement_timeout_seconds = 2.5
    settings.db_retry_attempts = 3
    settings.retry_backoff_base_seconds = 0.02
    settings.retry_backoff_max_seconds = 0.4
    settings.breaker_failure_threshold = 7
    settings.breaker_reset_seconds = 15.0
    settings.breaker_half_open_max_calls = 2

    policy = build_policies(settings)["mysql"]

    assert policy.call_timeout_seconds == 3.5
    assert policy.attempts == 3
    assert policy.backoff_base_seconds == 0.02
    assert policy.backoff_max_seconds == 0.4
    assert policy.breaker_failure_threshold == 7
    assert policy.breaker_reset_seconds == 15.0
    assert policy.breaker_half_open_max_calls == 2


def test_the_cache_policy_is_bounded_by_the_socket_timeout(settings) -> None:
    """Redis has no statement timeout to sit inside. Its own socket bound is the whole budget."""

    settings.redis_socket_timeout = 1.5

    assert build_policies(settings)["redis"].call_timeout_seconds == 1.5


def test_the_cache_is_never_retried(settings) -> None:
    """A cache miss costs one database read. A cache retry costs another socket timeout first."""

    assert build_policies(settings)["redis"].attempts == 1


def test_a_policy_is_frozen(settings) -> None:
    """A policy read per request must not be mutable from a request."""

    policy = build_policies(settings)["mysql"]

    with pytest.raises(Exception):  # noqa: B017 - dataclasses raise FrozenInstanceError
        policy.attempts = 99  # type: ignore[misc]


# --- the bound that matters most ----------------------------------------------------------------


def test_the_call_timeout_must_exceed_the_statement_timeout(settings) -> None:
    """Otherwise the client abandons the query before the server kills it.

    The connection then stays pinned to a statement nobody is waiting for, and the pool loses it
    for the length of the query rather than the length of the timeout.
    """

    settings.db_statement_timeout_seconds = 2.0
    settings.db_call_timeout_seconds = 2.0

    with pytest.raises(ValueError, match="DB_CALL_TIMEOUT_SECONDS"):
        build_policies(settings)


def test_the_error_names_both_sides_of_that_bound(settings) -> None:
    settings.db_statement_timeout_seconds = 5.0
    settings.db_call_timeout_seconds = 1.0

    with pytest.raises(ValueError, match="DB_STATEMENT_TIMEOUT_SECONDS"):
        build_policies(settings)


# --- every unbounded form is refused by name ----------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "variable"),
    [
        ("db_connect_timeout_seconds", 0.0, "DB_CONNECT_TIMEOUT_SECONDS"),
        ("db_connect_timeout_seconds", -1.0, "DB_CONNECT_TIMEOUT_SECONDS"),
        ("db_statement_timeout_seconds", 0.0, "DB_STATEMENT_TIMEOUT_SECONDS"),
        ("db_call_timeout_seconds", 0.0, "DB_CALL_TIMEOUT_SECONDS"),
        ("db_retry_attempts", 0, "DB_RETRY_ATTEMPTS"),
        ("db_retry_attempts", -1, "DB_RETRY_ATTEMPTS"),
        ("retry_backoff_base_seconds", 0.0, "RETRY_BACKOFF_BASE_SECONDS"),
        ("breaker_failure_threshold", 0, "BREAKER_FAILURE_THRESHOLD"),
        ("breaker_reset_seconds", 0.0, "BREAKER_RESET_SECONDS"),
        ("breaker_half_open_max_calls", 0, "BREAKER_HALF_OPEN_MAX_CALLS"),
        ("redis_socket_timeout", 0.0, "REDIS_SOCKET_TIMEOUT"),
    ],
)
def test_an_unbounded_value_names_its_environment_variable(
    settings, field: str, value: float, variable: str
) -> None:
    setattr(settings, field, value)

    with pytest.raises(ValueError, match=variable):
        build_policies(settings)


def test_a_backoff_cap_below_the_base_is_refused(settings) -> None:
    """A cap under the base is a contradiction, and tenacity would silently take the cap."""

    settings.retry_backoff_base_seconds = 0.5
    settings.retry_backoff_max_seconds = 0.1

    with pytest.raises(ValueError, match="RETRY_BACKOFF_MAX_SECONDS"):
        build_policies(settings)


def test_the_retry_budget_cannot_outlast_the_call_timeout(settings) -> None:
    """Three attempts of a 3 s call is a 9 s request, whatever the caller was promised.

    The bound is stated against the call timeout so the arithmetic is visible in the message
    rather than discovered under load.
    """

    settings.db_call_timeout_seconds = 3.0
    settings.db_statement_timeout_seconds = 2.0
    settings.db_retry_attempts = 20
    settings.retry_backoff_base_seconds = 0.05
    settings.retry_backoff_max_seconds = 0.5

    with pytest.raises(ValueError, match="DB_RETRY_ATTEMPTS"):
        build_policies(settings)


# --- the defaults ------------------------------------------------------------------------------


def test_the_defaults_are_bounded(settings) -> None:
    """The default matters more than the knob: most deployments never set one."""

    policies = build_policies(settings)

    for policy in policies.values():
        assert policy.call_timeout_seconds > 0
        assert policy.attempts >= 1
        assert policy.breaker_failure_threshold >= 1
        assert policy.breaker_reset_seconds > 0


def test_the_default_call_timeout_sits_inside_the_readiness_budget(settings) -> None:
    """A readiness probe that outlives its own timeout reports nothing useful."""

    assert build_policies(settings)["mysql"].call_timeout_seconds <= 5.0
