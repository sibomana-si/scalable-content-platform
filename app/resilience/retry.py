"""Retries for the failures a second attempt can fix, and for nothing else.

A retry is the cheapest fix for a transient fault and the most expensive mistake for a
permanent one. Two rules keep the difference visible:

- :func:`is_transient` decides what is worth a second attempt. A dropped connection is a fault.
  A constraint violation is an answer, and the second attempt breaks the same constraint one
  round trip later.
- Reads retry. Writes do not, because nothing downstream can tell a retried write from two
  writes. The write policy carries ``attempts == 1``, so the rule is data, not a branch.

The waits use full jitter — each one is drawn from ``[0, min(base * 2**n, cap)]``. Waiting the
full exponential every time re-synchronizes every replica onto the same instant, which is the
stampede the backoff exists to prevent.
"""

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy.exc import DisconnectionError, InterfaceError, OperationalError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_random_exponential

from app.observability.metrics import METRICS, Metrics, observe_retry
from app.resilience.policy import DependencyPolicy
from app.services.exceptions import DependencyUnavailableError, UpstreamTimeoutError

T = TypeVar("T")

# A fault: the dependency did not answer, or lost the connection it was answering on.
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    OperationalError,
    InterfaceError,
    DisconnectionError,
    RedisConnectionError,
    RedisTimeoutError,
    TimeoutError,
    OSError,
)

# An answer, however unwelcome. Everything not named above is one, including every `DomainError`.
TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (TimeoutError, RedisTimeoutError)


def is_transient(error: BaseException) -> bool:
    """Whether a second attempt could plausibly give a different result."""

    return isinstance(error, TRANSIENT_ERRORS)


def translate(error: BaseException, *, dependency: str, retry_after: float) -> Exception:
    """Turn a driver fault into the domain error the API layer knows how to answer with.

    The two cases stay apart all the way to the response: a dependency that answers late
    becomes 504, and one that refuses or is circuit-broken becomes 503.
    """

    if isinstance(error, TIMEOUT_ERRORS):
        return UpstreamTimeoutError(
            f"{dependency} did not answer inside its timeout", retry_after=retry_after
        )
    return DependencyUnavailableError(f"{dependency} is unavailable", retry_after=retry_after)


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    policy: DependencyPolicy,
    *,
    dependency: str,
    on_retry: Callable[[], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    metrics: Metrics = METRICS,
) -> T:
    """Call ``operation``, retrying transient faults inside the policy budget.

    ``on_retry`` runs before every attempt after the first. The database repositories pass
    ``session.rollback`` there: a failed statement leaves the transaction unusable, so without
    it the second attempt fails with ``PendingRollbackError`` and proves only that the session
    is broken.
    """
    if sleep is not None:
        retrying: AsyncRetrying = AsyncRetrying(
            stop=stop_after_attempt(policy.attempts),
            wait=wait_random_exponential(
                multiplier=policy.backoff_base_seconds, max=policy.backoff_max_seconds
            ),
            retry=retry_if_exception(is_transient),
            reraise=True,
            sleep=sleep,
        )
    else:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(policy.attempts),
            wait=wait_random_exponential(
                multiplier=policy.backoff_base_seconds, max=policy.backoff_max_seconds
            ),
            retry=retry_if_exception(is_transient),
            reraise=True,
        )
    attempts = 0
    result: Any = None

    try:
        async for attempt in retrying:
            with attempt:
                attempts += 1
                if attempts > 1 and on_retry is not None:
                    await on_retry()
                result = await operation()
            if not attempt.retry_state.outcome.failed:  # type: ignore[union-attr]
                attempt.retry_state.set_result(result)
    except Exception as error:
        if not is_transient(error):
            raise
        if attempts > 1:
            observe_retry(dependency, "exhausted", metrics=metrics)
        raise translate(
            error, dependency=dependency, retry_after=policy.breaker_reset_seconds
        ) from error

    if attempts > 1:
        observe_retry(dependency, "success", metrics=metrics)
    return result
