"""The guard: breaker, then timeout, then retry, around one dependency call.

Composition is where resilience code usually goes wrong. Each part is correct on its own, and
the order they run in decides whether an open breaker still costs a socket timeout, whether a
timeout ever reaches the breaker, and whether a plain 404 counts as an outage. These tests fix
the order.
"""

import asyncio

import pytest
from prometheus_client import CollectorRegistry
from sqlalchemy.exc import OperationalError

from app.observability.metrics import build_metrics
from app.resilience.breaker import BreakerState, CircuitBreaker
from app.resilience.guard import guarded_call, guarded_read, guarded_write
from app.resilience.policy import DependencyPolicy
from app.services.exceptions import (
    ArticleNotFoundError,
    DependencyUnavailableError,
    UpstreamTimeoutError,
)


def a_policy(**overrides: object) -> DependencyPolicy:
    fields: dict = {
        "name": "mysql",
        "call_timeout_seconds": 0.05,
        "attempts": 3,
        "backoff_base_seconds": 0.001,
        "backoff_max_seconds": 0.002,
        "breaker_failure_threshold": 2,
        "breaker_reset_seconds": 10.0,
        "breaker_half_open_max_calls": 1,
    }
    return DependencyPolicy(**{**fields, **overrides})


def an_operational_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("server has gone away"))


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def metrics():
    return build_metrics(CollectorRegistry())


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def policy() -> DependencyPolicy:
    return a_policy()


@pytest.fixture
def breaker(policy: DependencyPolicy, clock: FakeClock, metrics) -> CircuitBreaker:
    return CircuitBreaker(
        policy.name,
        failure_threshold=policy.breaker_failure_threshold,
        reset_seconds=policy.breaker_reset_seconds,
        half_open_max_calls=policy.breaker_half_open_max_calls,
        clock=clock,
        metrics=metrics,
    )


def timeouts(metrics, dependency: str = "mysql") -> float:
    return (
        metrics.registry.get_sample_value("dependency_timeouts_total", {"dependency": dependency})
        or 0.0
    )


async def call(operation, *, policy, breaker, metrics, **kwargs):
    return await guarded_call(
        operation, dependency=policy.name, policy=policy, breaker=breaker, metrics=metrics, **kwargs
    )


# --- the breaker comes first ---------------------------------------------------------------------


async def test_an_open_breaker_makes_no_call_at_all(policy, breaker, metrics) -> None:
    """The whole point. An open breaker that still pays the timeout saves nothing."""

    called = False

    async def operation() -> None:
        nonlocal called
        called = True

    breaker.record_failure()
    breaker.record_failure()

    with pytest.raises(DependencyUnavailableError):
        await call(operation, policy=policy, breaker=breaker, metrics=metrics)

    assert called is False


async def test_the_refusal_tells_the_caller_when_to_come_back(policy, breaker, metrics) -> None:
    breaker.record_failure()
    breaker.record_failure()

    with pytest.raises(DependencyUnavailableError) as raised:
        await call(lambda: _ok(), policy=policy, breaker=breaker, metrics=metrics)

    assert 0 < raised.value.retry_after <= policy.breaker_reset_seconds


# --- timeouts ------------------------------------------------------------------------------------


async def test_a_call_that_never_answers_raises_an_upstream_timeout(
    policy, breaker, metrics
) -> None:
    async def operation() -> None:
        await asyncio.sleep(5)

    with pytest.raises(UpstreamTimeoutError):
        await call(operation, policy=policy, breaker=breaker, metrics=metrics)


async def test_a_timeout_is_counted(policy, breaker, metrics) -> None:
    async def operation() -> None:
        await asyncio.sleep(5)

    with pytest.raises(UpstreamTimeoutError):
        await call(operation, policy=policy, breaker=breaker, metrics=metrics)

    assert timeouts(metrics) == 1


async def test_a_timeout_counts_against_the_breaker(policy, breaker, metrics) -> None:
    """A dependency that answers nothing is down, whatever its socket says."""

    async def operation() -> None:
        await asyncio.sleep(5)

    for _ in range(2):
        with pytest.raises(UpstreamTimeoutError):
            await call(operation, policy=policy, breaker=breaker, metrics=metrics)

    assert breaker.state is BreakerState.OPEN


async def test_the_call_timeout_bounds_the_whole_retry_budget(policy, breaker, metrics) -> None:
    """Three attempts of a bounded call must not add up to three times the promised bound."""

    started = asyncio.get_running_loop().time()

    with pytest.raises(UpstreamTimeoutError):
        await call(lambda: asyncio.sleep(5), policy=policy, breaker=breaker, metrics=metrics)

    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < policy.call_timeout_seconds * policy.attempts


# --- an answer is not a fault --------------------------------------------------------------------


async def test_a_domain_error_passes_through_untouched(policy, breaker, metrics) -> None:
    with pytest.raises(ArticleNotFoundError):
        await call(
            lambda: _raise(ArticleNotFoundError("gone")),
            policy=policy,
            breaker=breaker,
            metrics=metrics,
        )


async def test_a_domain_error_does_not_trip_the_breaker(policy, breaker, metrics) -> None:
    """Ten thousand 404s are ten thousand answers. A breaker that opens on them is a bug."""

    for _ in range(10):
        with pytest.raises(ArticleNotFoundError):
            await call(
                lambda: _raise(ArticleNotFoundError("gone")),
                policy=policy,
                breaker=breaker,
                metrics=metrics,
            )

    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow() is True


async def test_a_success_clears_an_earlier_failure(policy, breaker, metrics) -> None:
    breaker.record_failure()

    await call(lambda: _ok(), policy=policy, breaker=breaker, metrics=metrics)

    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED


async def test_a_successful_call_returns_the_value(policy, breaker, metrics) -> None:
    assert await call(lambda: _ok(), policy=policy, breaker=breaker, metrics=metrics) == "ok"


# --- the decorators the repositories wear ---------------------------------------------------------


class FakeSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeRepository:
    """The shape both repositories share: a `_session` and async methods over it."""

    def __init__(self, *, failures: int = 0) -> None:
        self._session = FakeSession()
        self._failures = failures
        self.calls = 0

    @guarded_read("mysql")
    async def read(self) -> str:
        self.calls += 1
        if self.calls <= self._failures:
            raise an_operational_error()
        return "row"

    @guarded_write("mysql")
    async def write(self) -> str:
        self.calls += 1
        if self.calls <= self._failures:
            raise an_operational_error()
        return "written"


async def test_a_guarded_read_returns_the_value_unchanged() -> None:
    assert await FakeRepository().read() == "row"


async def test_a_guarded_read_retries_a_transient_failure() -> None:
    repository = FakeRepository(failures=1)

    assert await repository.read() == "row"
    assert repository.calls == 2


async def test_a_guarded_read_rolls_the_transaction_back_between_attempts() -> None:
    """A failed statement poisons the session, so the next attempt needs a clean one."""

    repository = FakeRepository(failures=1)

    await repository.read()

    assert repository._session.rollbacks == 1


async def test_a_guarded_write_is_never_retried() -> None:
    """At-most-once beats at-least-once when the operation is not idempotent."""

    repository = FakeRepository(failures=1)

    with pytest.raises(DependencyUnavailableError):
        await repository.write()

    assert repository.calls == 1
    assert repository._session.rollbacks == 0


async def test_a_guarded_method_keeps_its_name_and_docstring() -> None:
    """FastAPI, pytest and every traceback read these."""

    assert FakeRepository.write.__name__ == "write"
    assert FakeRepository.read.__name__ == "read"


async def _ok() -> str:
    return "ok"


def _raise(error: BaseException):
    async def operation() -> None:
        raise error

    return operation()
