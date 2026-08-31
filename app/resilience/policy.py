"""The timeout, retry and breaker budget for each dependency, validated before use.

`app/db/session.py:build_engine_kwargs` applied to the whole call path. Every bound here makes
a failure bounded, and every `ValueError` names the environment variable that caused it: a
misconfigured ceiling that starts anyway is worse than one that refuses to start, because the
system runs until the load arrives.
"""

from dataclasses import dataclass

from app.config import Settings, get_settings


@dataclass(frozen=True)
class DependencyPolicy:
    """What one dependency is allowed to cost.

    Frozen because it is read on every request and shared between them.
    """

    name: str
    call_timeout_seconds: float
    attempts: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    breaker_failure_threshold: int
    breaker_reset_seconds: float
    breaker_half_open_max_calls: int

    def worst_case_backoff_seconds(self) -> float:
        """The longest the waits between attempts can add up to, under full jitter."""

        return sum(
            min(self.backoff_base_seconds * 2.0**index, self.backoff_max_seconds)
            for index in range(max(self.attempts - 1, 0))
        )


def _require_positive(value: float, variable: str, reason: str) -> None:
    if value <= 0:
        raise ValueError(f"{variable} must be positive; {reason}")


def build_policies(settings: Settings | None = None) -> dict[str, DependencyPolicy]:
    """One policy per dependency the request path touches, or a `ValueError` naming the fault."""

    settings = settings or get_settings()

    _require_positive(
        settings.db_connect_timeout_seconds,
        "DB_CONNECT_TIMEOUT_SECONDS",
        "aiomysql reads a non-positive value as no timeout, so a blackholed host holds the "
        "request until the kernel gives up.",
    )
    _require_positive(
        settings.db_statement_timeout_seconds,
        "DB_STATEMENT_TIMEOUT_SECONDS",
        "MySQL reads max_execution_time=0 as no limit, so a runaway query keeps its "
        "connection until it finishes.",
    )
    _require_positive(
        settings.db_call_timeout_seconds,
        "DB_CALL_TIMEOUT_SECONDS",
        "a call with no ceiling turns one unresponsive dependency into an unresponsive API.",
    )
    _require_positive(
        settings.redis_socket_timeout,
        "REDIS_SOCKET_TIMEOUT",
        "an unbounded cache read makes the cache a dependency instead of an optimization.",
    )
    _require_positive(
        settings.retry_backoff_base_seconds,
        "RETRY_BACKOFF_BASE_SECONDS",
        "a zero base retries at once, which adds load to a dependency that is already failing.",
    )
    _require_positive(
        settings.breaker_reset_seconds,
        "BREAKER_RESET_SECONDS",
        "an open circuit that never probes never closes.",
    )
    if settings.db_retry_attempts < 1:
        raise ValueError(
            "DB_RETRY_ATTEMPTS must be at least 1; it counts attempts, not retries, so a "
            "value below 1 makes no call at all."
        )
    if settings.breaker_failure_threshold < 1:
        raise ValueError(
            "BREAKER_FAILURE_THRESHOLD must be at least 1; a threshold of 0 opens the circuit "
            "before anything fails."
        )
    if settings.breaker_half_open_max_calls < 1:
        raise ValueError(
            "BREAKER_HALF_OPEN_MAX_CALLS must be at least 1; a half-open circuit that admits "
            "no probe never learns that the dependency came back."
        )
    if settings.retry_backoff_max_seconds < settings.retry_backoff_base_seconds:
        raise ValueError(
            "RETRY_BACKOFF_MAX_SECONDS must not be below RETRY_BACKOFF_BASE_SECONDS; the cap "
            "would silently replace the base and every wait would be the same length."
        )
    if settings.db_call_timeout_seconds <= settings.db_statement_timeout_seconds:
        raise ValueError(
            "DB_CALL_TIMEOUT_SECONDS must exceed DB_STATEMENT_TIMEOUT_SECONDS; otherwise the "
            "client abandons the query before the server kills it, and the connection stays "
            "pinned to work nobody is waiting for."
        )

    database = DependencyPolicy(
        name="mysql",
        call_timeout_seconds=settings.db_call_timeout_seconds,
        attempts=settings.db_retry_attempts,
        backoff_base_seconds=settings.retry_backoff_base_seconds,
        backoff_max_seconds=settings.retry_backoff_max_seconds,
        breaker_failure_threshold=settings.breaker_failure_threshold,
        breaker_reset_seconds=settings.breaker_reset_seconds,
        breaker_half_open_max_calls=settings.breaker_half_open_max_calls,
    )
    if database.worst_case_backoff_seconds() >= database.call_timeout_seconds:
        raise ValueError(
            f"DB_RETRY_ATTEMPTS is too high for the budget: the waits between "
            f"{database.attempts} attempts can reach "
            f"{database.worst_case_backoff_seconds():.2f}s, which is already the whole "
            f"{database.call_timeout_seconds}s DB_CALL_TIMEOUT_SECONDS. The later attempts "
            f"could never run."
        )

    cache = DependencyPolicy(
        name="redis",
        # Redis has no statement timeout to sit inside, so its own socket bound is the budget.
        call_timeout_seconds=settings.redis_socket_timeout,
        # A cache miss costs one database read. A cache retry costs another socket timeout
        # first, and then the same database read.
        attempts=1,
        backoff_base_seconds=settings.retry_backoff_base_seconds,
        backoff_max_seconds=settings.retry_backoff_max_seconds,
        breaker_failure_threshold=settings.breaker_failure_threshold,
        breaker_reset_seconds=settings.breaker_reset_seconds,
        breaker_half_open_max_calls=settings.breaker_half_open_max_calls,
    )
    return {"mysql": database, "redis": cache}


def get_policy(dependency: str, settings: Settings | None = None) -> DependencyPolicy:
    """The policy for one dependency, built from the process settings."""

    return build_policies(settings)[dependency]
