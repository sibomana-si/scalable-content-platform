"""The composition: circuit breaker, then timeout, then retry, around one dependency call.

The order is the design. Each part is correct alone, and the order decides three things a
reader would otherwise have to guess:

1. The breaker runs first, so an open circuit costs no socket and no wait. A breaker consulted
   after the timeout saves nothing, which is the usual way this pattern is written and wasted.
2. The timeout wraps the retries, so the budget the caller was promised covers every attempt.
   Three attempts of a three-second call must not become a nine-second request.
3. A domain error passes straight through and never reaches the breaker. Ten thousand 404s are
   ten thousand answers, and a breaker that opens on them takes the API down over working
   queries.

Reads use :func:`guarded_read`, which retries and rolls the session back between attempts.
Writes use :func:`guarded_write`, which never retries.
"""

import asyncio
import functools
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, TypeVar

from app.observability.metrics import METRICS, Metrics, observe_dependency_timeout
from app.resilience.breaker import CircuitBreaker, get_breaker
from app.resilience.policy import DependencyPolicy, build_policies
from app.resilience.retry import translate, with_retry
from app.services.exceptions import DependencyDownError, DependencyUnavailableError

T = TypeVar("T")


@functools.lru_cache
def get_policies() -> dict[str, DependencyPolicy]:
    """The validated policy set, built once. Cleared with `get_policies.cache_clear()`."""

    return build_policies()


async def guarded_call(
    operation: Callable[[], Awaitable[T]],
    *,
    dependency: str,
    policy: DependencyPolicy | None = None,
    breaker: CircuitBreaker | None = None,
    on_retry: Callable[[], Awaitable[None]] | None = None,
    retry: bool = True,
    metrics: Metrics = METRICS,
) -> T:
    """Run ``operation`` under the policy for ``dependency``.

    Raises ``UpstreamTimeoutError`` when the dependency answers nothing inside the budget, and
    ``DependencyUnavailableError`` when it refuses the call or the circuit is open. Every other
    error the operation raises is the dependency's answer, and passes through unchanged.
    """

    policy = policy or get_policies()[dependency]
    breaker = breaker or get_breaker(dependency, policy)

    if not breaker.allow():
        raise DependencyUnavailableError(
            f"{dependency} is unavailable: the circuit is open",
            retry_after=breaker.seconds_until_probe(),
        )

    effective = policy if retry else replace(policy, attempts=1)
    result: Any = None
    try:
        async with asyncio.timeout(policy.call_timeout_seconds):
            result = await with_retry(
                operation,
                effective,
                dependency=dependency,
                on_retry=on_retry if retry else None,
                metrics=metrics,
            )
    except TimeoutError as error:
        # The outer budget expired mid-attempt. The retry layer never saw it, so count it here.
        observe_dependency_timeout(dependency, metrics=metrics)
        breaker.record_failure()
        raise translate(
            error, dependency=dependency, retry_after=policy.breaker_reset_seconds
        ) from error
    except DependencyDownError as error:
        if isinstance(error, DependencyUnavailableError):
            breaker.record_failure()
            raise
        observe_dependency_timeout(dependency, metrics=metrics)
        breaker.record_failure()
        raise
    except Exception:
        # The dependency answered, and the answer was an error the caller has to handle. That
        # is a working dependency, so the breaker hears about it as a success.
        breaker.record_success()
        raise

    breaker.record_success()
    return result


def _guarded(dependency: str, *, retry: bool) -> Callable[[Callable], Callable]:
    """Wrap an async repository method in the guard for ``dependency``."""

    def decorator(method: Callable) -> Callable:
        @functools.wraps(method)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            session = getattr(self, "_session", None)
            return await guarded_call(
                lambda: method(self, *args, **kwargs),
                dependency=dependency,
                on_retry=session.rollback if retry and session is not None else None,
                retry=retry,
            )

        return wrapper

    return decorator


def guarded_read(dependency: str) -> Callable[[Callable], Callable]:
    """Guard a read: breaker, timeout, and retries with a rollback between attempts.

    A read is idempotent, so a retry costs a round trip and risks nothing.
    """

    return _guarded(dependency, retry=True)


def guarded_write(dependency: str) -> Callable[[Callable], Callable]:
    """Guard a write: breaker and timeout, never a retry.

    A retried write is a second write. The compare-and-set statements would make the duplicate
    harmless, but `create` would not, and at-most-once is the property worth keeping.
    """

    return _guarded(dependency, retry=False)
