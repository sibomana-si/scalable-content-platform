"""A circuit breaker per dependency.

A timeout bounds one call. It does not stop the next thousand callers from each paying the same
timeout against the same dead dependency, which is how a slow dependency becomes a queue and
the queue becomes an outage. The breaker turns the second failure and every failure after it
into an immediate answer.

The state machine is a pure decision over a monotonic clock, so the clock is injected and the
tests drive it directly. Nothing here awaits, and nothing here holds a lock: the app is a
single-threaded event loop per process, so a breaker is only ever read and written between
awaits, never during one.
"""

import time
from collections.abc import Callable
from enum import Enum

from app.observability.metrics import (
    METRICS,
    Metrics,
    observe_breaker_transition,
    set_breaker_state,
)
from app.resilience.policy import DependencyPolicy, build_policies


class BreakerState(Enum):
    """What the breaker does with the next call."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Fails fast while a dependency is down, and probes until it comes back."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int,
        reset_seconds: float,
        half_open_max_calls: int,
        clock: Callable[[], float] = time.monotonic,
        metrics: Metrics = METRICS,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._half_open_max_calls = half_open_max_calls
        self._clock = clock
        self._metrics = metrics
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probes = 0
        # Publish the starting state, so a graph tells a closed breaker from a dependency
        # nobody has called yet. This is not a transition, so nothing counts it.
        set_breaker_state(self.name, self._state.value, metrics=self._metrics)

    @property
    def state(self) -> BreakerState:
        return self._state

    def allow(self) -> bool:
        """Whether the caller may make the call now.

        Reading this moves the breaker from open to half-open when the reset interval has
        passed, so the first caller after the interval becomes the probe.
        """

        if self._state is BreakerState.CLOSED:
            return True
        if self._state is BreakerState.OPEN:
            if self._clock() - self._opened_at < self._reset_seconds:
                return False
            self._to_half_open()
        if self._probes >= self._half_open_max_calls:
            return False
        self._probes += 1
        return True

    def record_success(self) -> None:
        """The dependency answered."""

        if self._state is BreakerState.HALF_OPEN:
            self._to_closed()
            return
        # Two failures an hour apart are not an outage, so the count is consecutive failures.
        self._failures = 0

    def record_failure(self) -> None:
        """The dependency did not answer, or answered with a fault."""

        if self._state is BreakerState.HALF_OPEN:
            self._to_open()
            return
        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._to_open()

    def seconds_until_probe(self) -> float:
        """How long the caller should wait before the circuit admits another call.

        This is the `Retry-After` value. It is never negative and never zero, because a client
        told to come back immediately comes back immediately.
        """

        if self._state is not BreakerState.OPEN:
            return 0.0
        remaining = self._reset_seconds - (self._clock() - self._opened_at)
        return max(remaining, 0.001)

    def _to_open(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._probes = 0
        self._failures = 0
        self._transition()

    def _to_half_open(self) -> None:
        self._state = BreakerState.HALF_OPEN
        self._probes = 0
        self._transition()

    def _to_closed(self) -> None:
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._probes = 0
        self._transition()

    def _transition(self) -> None:
        observe_breaker_transition(self.name, self._state.value, metrics=self._metrics)


# One breaker per dependency, for the life of the process. A per-request breaker would count to
# one and forget, which is a breaker that never opens.
_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(dependency: str, policy: DependencyPolicy | None = None) -> CircuitBreaker:
    """The process-wide breaker for one dependency, built on first use."""

    breaker = _BREAKERS.get(dependency)
    if breaker is None:
        policy = policy or build_policies()[dependency]
        breaker = CircuitBreaker(
            dependency,
            failure_threshold=policy.breaker_failure_threshold,
            reset_seconds=policy.breaker_reset_seconds,
            half_open_max_calls=policy.breaker_half_open_max_calls,
        )
        _BREAKERS[dependency] = breaker
    return breaker


def reset_breakers() -> None:
    """Forget every breaker. For tests: state that outlives a process outlives a test too."""

    _BREAKERS.clear()
