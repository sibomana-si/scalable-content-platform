"""The circuit-breaker state machine, driven by an injected clock.

Not a single `asyncio.sleep` appears here on purpose. The breaker is a pure decision — count
failures, compare a deadline, pick a state — so a test that waits on real time would be slow,
flaky on a loaded machine, and sensitive to the CPU governor, while proving nothing extra.
"""

import pytest
from prometheus_client import CollectorRegistry

from app.observability.metrics import build_metrics
from app.resilience.breaker import BreakerState, CircuitBreaker, get_breaker, reset_breakers


class FakeClock:
    """A monotonic clock that only moves when a test says so."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def metrics():
    return build_metrics(CollectorRegistry())


@pytest.fixture
def breaker(clock: FakeClock, metrics) -> CircuitBreaker:
    return CircuitBreaker(
        "mysql",
        failure_threshold=3,
        reset_seconds=10.0,
        half_open_max_calls=1,
        clock=clock,
        metrics=metrics,
    )


def gauge(metrics, dependency: str = "mysql") -> float:
    return metrics.registry.get_sample_value("circuit_breaker_state", {"dependency": dependency})


def transitions(metrics, to_state: str, dependency: str = "mysql") -> float:
    return (
        metrics.registry.get_sample_value(
            "circuit_breaker_transitions_total",
            {"dependency": dependency, "to_state": to_state},
        )
        or 0.0
    )


# --- closed -------------------------------------------------------------------------------------


def test_a_new_breaker_is_closed_and_admits_calls(breaker: CircuitBreaker) -> None:
    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow() is True


def test_failures_under_the_threshold_keep_it_closed(breaker: CircuitBreaker) -> None:
    breaker.record_failure()
    breaker.record_failure()

    assert breaker.state is BreakerState.CLOSED


def test_a_success_while_closed_clears_the_failure_count(breaker: CircuitBreaker) -> None:
    """Two failures an hour apart are not an outage, and must not add up to one."""

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()

    assert breaker.state is BreakerState.CLOSED


# --- opening ------------------------------------------------------------------------------------


def test_the_threshold_opens_it(breaker: CircuitBreaker) -> None:
    for _ in range(3):
        breaker.record_failure()

    assert breaker.state is BreakerState.OPEN


def test_an_open_breaker_refuses_every_call(breaker: CircuitBreaker) -> None:
    for _ in range(3):
        breaker.record_failure()

    assert breaker.allow() is False
    assert breaker.allow() is False


def test_an_open_breaker_stays_open_until_the_reset_interval_elapses(
    breaker: CircuitBreaker, clock: FakeClock
) -> None:
    for _ in range(3):
        breaker.record_failure()

    clock.advance(9.9)

    assert breaker.allow() is False


# --- half open ----------------------------------------------------------------------------------


def test_the_reset_interval_admits_one_probe(breaker: CircuitBreaker, clock: FakeClock) -> None:
    for _ in range(3):
        breaker.record_failure()
    clock.advance(10.0)

    assert breaker.allow() is True
    assert breaker.state is BreakerState.HALF_OPEN


def test_half_open_admits_no_more_than_the_probe_limit(
    breaker: CircuitBreaker, clock: FakeClock
) -> None:
    """A thundering herd of probes against a recovering database re-opens it at once."""

    for _ in range(3):
        breaker.record_failure()
    clock.advance(10.0)

    assert breaker.allow() is True
    assert breaker.allow() is False


def test_a_half_open_success_closes_it_and_resets_the_count(
    breaker: CircuitBreaker, clock: FakeClock
) -> None:
    for _ in range(3):
        breaker.record_failure()
    clock.advance(10.0)
    breaker.allow()

    breaker.record_success()

    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED


def test_a_half_open_failure_reopens_it_and_restarts_the_clock(
    breaker: CircuitBreaker, clock: FakeClock
) -> None:
    for _ in range(3):
        breaker.record_failure()
    clock.advance(10.0)
    breaker.allow()

    breaker.record_failure()

    assert breaker.state is BreakerState.OPEN
    clock.advance(9.9)
    assert breaker.allow() is False
    clock.advance(0.2)
    assert breaker.allow() is True


# --- what the dashboards see --------------------------------------------------------------------


def test_the_gauge_follows_every_move(breaker: CircuitBreaker, clock: FakeClock, metrics) -> None:
    assert gauge(metrics) == 0

    for _ in range(3):
        breaker.record_failure()
    assert gauge(metrics) == 2

    clock.advance(10.0)
    breaker.allow()
    assert gauge(metrics) == 1

    breaker.record_success()
    assert gauge(metrics) == 0


def test_the_transition_counter_records_each_change_once(
    breaker: CircuitBreaker, clock: FakeClock, metrics
) -> None:
    for _ in range(3):
        breaker.record_failure()
    clock.advance(10.0)
    breaker.allow()
    breaker.record_success()

    assert transitions(metrics, "open") == 1
    assert transitions(metrics, "half_open") == 1
    assert transitions(metrics, "closed") == 1


def test_refusing_a_call_is_not_a_transition(
    breaker: CircuitBreaker, clock: FakeClock, metrics
) -> None:
    """An open breaker refusing 10,000 requests is one event, not 10,000."""

    for _ in range(3):
        breaker.record_failure()
    for _ in range(10):
        breaker.allow()

    assert transitions(metrics, "open") == 1


# --- the registry -------------------------------------------------------------------------------


def test_a_dependency_gets_the_same_breaker_every_time() -> None:
    """The breaker is worth having only because it outlives the request."""

    reset_breakers()
    try:
        assert get_breaker("mysql") is get_breaker("mysql")
        assert get_breaker("mysql") is not get_breaker("redis")
    finally:
        reset_breakers()


def test_resetting_the_registry_forgets_every_breaker() -> None:
    reset_breakers()
    first = get_breaker("mysql")
    reset_breakers()

    assert get_breaker("mysql") is not first
