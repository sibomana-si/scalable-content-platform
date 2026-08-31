"""Retry behavior: what is retried, how often, how long between attempts, and what surfaces.

A retry is the cheapest fix for a transient fault and the most expensive mistake for a
permanent one. Every test here fixes one half of that: the classifier decides what is worth a
second attempt, and the bounds decide that a second attempt cannot become a stampede.

The sleep function is injected, so the timing rules are asserted without waiting on them.
"""

import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from app.observability.metrics import build_metrics
from app.resilience.policy import DependencyPolicy
from app.resilience.retry import is_transient, with_retry
from app.services.exceptions import (
    ArticleNotFoundError,
    DependencyUnavailableError,
    UpstreamTimeoutError,
)


def a_policy(**overrides: object) -> DependencyPolicy:
    fields: dict = {
        "name": "mysql",
        "call_timeout_seconds": 3.0,
        "attempts": 3,
        "backoff_base_seconds": 0.01,
        "backoff_max_seconds": 0.1,
        "breaker_failure_threshold": 5,
        "breaker_reset_seconds": 10.0,
        "breaker_half_open_max_calls": 1,
    }
    return DependencyPolicy(**{**fields, **overrides})


def an_operational_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("server has gone away"))


class Sleeper:
    """Stands in for `asyncio.sleep` and remembers what it was asked to wait."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


@pytest.fixture
def sleeper() -> Sleeper:
    return Sleeper()


@pytest.fixture
def metrics():
    return build_metrics(CollectorRegistry())


def retries(metrics, outcome: str, dependency: str = "mysql") -> float:
    return (
        metrics.registry.get_sample_value(
            "dependency_retries_total", {"dependency": dependency, "outcome": outcome}
        )
        or 0.0
    )


# --- the happy path costs nothing ---------------------------------------------------------------


async def test_a_first_attempt_that_succeeds_never_sleeps(sleeper, metrics) -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await with_retry(
        operation, a_policy(), dependency="mysql", sleep=sleeper, metrics=metrics
    )

    assert result == "ok"
    assert calls == 1
    assert sleeper.waits == []
    assert retries(metrics, "success") == 0


async def test_a_transient_error_retries_and_then_succeeds(sleeper, metrics) -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise an_operational_error()
        return "ok"

    result = await with_retry(
        operation, a_policy(), dependency="mysql", sleep=sleeper, metrics=metrics
    )

    assert result == "ok"
    assert calls == 2
    assert len(sleeper.waits) == 1
    assert retries(metrics, "success") == 1


# --- the budget is a ceiling ---------------------------------------------------------------------


async def test_the_attempt_count_is_a_hard_cap(sleeper, metrics) -> None:
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise an_operational_error()

    with pytest.raises(DependencyUnavailableError):
        await with_retry(
            operation, a_policy(attempts=3), dependency="mysql", sleep=sleeper, metrics=metrics
        )

    assert calls == 3
    assert len(sleeper.waits) == 2
    assert retries(metrics, "exhausted") == 1


async def test_an_exhausted_timeout_surfaces_as_an_upstream_timeout(sleeper, metrics) -> None:
    """The caller needs to tell "the database said no" from "the database said nothing"."""

    async def operation() -> None:
        raise TimeoutError

    with pytest.raises(UpstreamTimeoutError) as raised:
        await with_retry(operation, a_policy(), dependency="mysql", sleep=sleeper, metrics=metrics)

    assert raised.value.retry_after > 0
    assert "mysql" in str(raised.value)


async def test_an_exhausted_connection_failure_surfaces_as_unavailable(sleeper, metrics) -> None:
    with pytest.raises(DependencyUnavailableError) as raised:
        await with_retry(
            lambda: _raise(an_operational_error()),
            a_policy(),
            dependency="mysql",
            sleep=sleeper,
            metrics=metrics,
        )

    assert raised.value.retry_after > 0


async def test_the_original_failure_is_kept_as_the_cause(sleeper, metrics) -> None:
    """A translated error with no `__cause__` costs the operator the stack trace."""

    original = an_operational_error()

    with pytest.raises(DependencyUnavailableError) as raised:
        await with_retry(
            lambda: _raise(original), a_policy(), dependency="mysql", sleep=sleeper, metrics=metrics
        )

    assert raised.value.__cause__ is original


# --- what must never be retried -------------------------------------------------------------------


async def test_a_constraint_violation_is_never_retried(sleeper, metrics) -> None:
    """The second attempt breaks the same constraint, one round trip later."""

    calls = 0
    original = IntegrityError("INSERT", {}, Exception("duplicate entry"))

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise original

    with pytest.raises(IntegrityError):
        await with_retry(operation, a_policy(), dependency="mysql", sleep=sleeper, metrics=metrics)

    assert calls == 1
    assert sleeper.waits == []
    assert retries(metrics, "exhausted") == 0


async def test_a_domain_error_passes_through_untouched(sleeper, metrics) -> None:
    """A missing article is an answer, not a fault."""

    with pytest.raises(ArticleNotFoundError):
        await with_retry(
            lambda: _raise(ArticleNotFoundError("gone")),
            a_policy(),
            dependency="mysql",
            sleep=sleeper,
            metrics=metrics,
        )

    assert sleeper.waits == []


async def test_a_single_attempt_policy_never_retries(sleeper, metrics) -> None:
    """The write policy. A retried write is a second write, and no test can prove it is not."""

    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        raise an_operational_error()

    with pytest.raises(DependencyUnavailableError):
        await with_retry(
            operation, a_policy(attempts=1), dependency="mysql", sleep=sleeper, metrics=metrics
        )

    assert calls == 1
    assert sleeper.waits == []


# --- backoff -----------------------------------------------------------------------------------


async def test_the_backoff_is_exponential_capped_and_jittered(sleeper, metrics) -> None:
    """Full jitter: each wait is drawn from `[0, min(base * 2**n, cap)]`.

    Waiting the full exponential every time re-synchronizes every replica onto the same instant,
    which is the stampede the backoff exists to prevent.
    """

    policy = a_policy(attempts=6, backoff_base_seconds=0.05, backoff_max_seconds=0.4)

    with pytest.raises(DependencyUnavailableError):
        await with_retry(
            lambda: _raise(an_operational_error()),
            policy,
            dependency="mysql",
            sleep=sleeper,
            metrics=metrics,
        )

    assert len(sleeper.waits) == 5
    for index, wait in enumerate(sleeper.waits):
        ceiling = min(policy.backoff_base_seconds * 2**index, policy.backoff_max_seconds)
        assert 0.0 <= wait <= ceiling


async def test_no_single_wait_exceeds_the_cap(sleeper, metrics) -> None:
    policy = a_policy(attempts=8, backoff_base_seconds=0.1, backoff_max_seconds=0.2)

    with pytest.raises(DependencyUnavailableError):
        await with_retry(
            lambda: _raise(an_operational_error()),
            policy,
            dependency="mysql",
            sleep=sleeper,
            metrics=metrics,
        )

    assert max(sleeper.waits) <= policy.backoff_max_seconds


# --- the hook that makes a retry legal on a session --------------------------------------------


async def test_the_hook_fires_between_attempts_only(sleeper, metrics) -> None:
    """A failed statement leaves the transaction unusable, so the hook rolls it back.

    Without it the second attempt fails with `PendingRollbackError`, and the retry proves
    nothing except that the session is broken.
    """

    events: list[str] = []

    async def on_retry() -> None:
        events.append("rollback")

    async def operation() -> str:
        events.append("attempt")
        if events.count("attempt") < 3:
            raise an_operational_error()
        return "ok"

    await with_retry(
        operation,
        a_policy(),
        dependency="mysql",
        on_retry=on_retry,
        sleep=sleeper,
        metrics=metrics,
    )

    assert events == ["attempt", "rollback", "attempt", "rollback", "attempt"]


async def test_the_hook_does_not_fire_when_the_first_attempt_succeeds(sleeper, metrics) -> None:
    fired = False

    async def on_retry() -> None:
        nonlocal fired
        fired = True

    await with_retry(
        lambda: _ok(),
        a_policy(),
        dependency="mysql",
        on_retry=on_retry,
        sleep=sleeper,
        metrics=metrics,
    )

    assert fired is False


# --- the classifier -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (an_operational_error(), True),
        (InterfaceError("SELECT 1", {}, Exception("connection closed")), True),
        (TimeoutError(), True),
        (TimeoutError(), True),
        (ConnectionResetError(), True),
        (OSError("no route to host"), True),
        (IntegrityError("INSERT", {}, Exception("duplicate")), False),
        (ArticleNotFoundError("gone"), False),
        (ValueError("a bug"), False),
    ],
)
def test_the_classifier_retries_faults_and_refuses_answers(error, expected) -> None:
    assert is_transient(error) is expected


async def _ok() -> str:
    return "ok"


def _raise(error: BaseException):
    """Raise inside a coroutine, so a lambda can stand in for a failing operation."""

    async def operation() -> None:
        raise error

    return operation()
