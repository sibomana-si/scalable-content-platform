"""Prometheus instrumentation: RED metrics for the API, latency for the database.

Every label value comes from a closed set or a router-supplied template. Prometheus stores one
time series per label combination, so a caller-controlled label (a raw URL, a SQL statement)
is an unbounded-memory hole in the scrape target — the two bucketing functions here,
:func:`route_label` and :func:`sql_operation`, are the guards, and both are unit-tested.

The collectors are built once at module scope on the default registry: the app factory is
called per test (and again at import of ``app.main``), and re-registering the same metric name
on the same registry raises ``Duplicated timeseries``. :func:`build_metrics` exists so tests
can own a private registry instead.
"""

import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import event

from app.observability.context import route_template

# `_created` gauges duplicate every series for information nothing here uses.
disable_created_metrics()

# Where Prometheus scrapes. Shared so the auth allowlist, the router and the
# self-instrumentation exclusion cannot drift apart.
METRICS_PATH = "/metrics"

# Orchestrator probes, excluded from the RED metrics. Counted, they dilute the availability
# ratio and the error budget with traffic no user ever sent, and they hold the denominator of
# ``NoTrafficReceived`` permanently non-zero — which is exactly the condition it exists to
# detect. Matched against the route label, not a ``/health`` prefix: unmatched paths collapse
# to ``__unmatched__``, so a scanner cannot slip a series past this under ``/health/anything``.
PROBE_PATHS = frozenset({"/health/live", "/health/ready"})

# The only values the ``query`` label may take.
SQL_OPERATIONS = frozenset({"select", "insert", "update", "delete", "other"})

# The only values the cache ``entity`` label may take. It names the key namespace, not the
# key: ``article:42`` as a label would mint one series per article.
CACHE_ENTITIES = frozenset({"article", "list", "other"})

# The only values the cache ``operation`` label may take. Each names a cache call site, so
# a degradation graph shows which half of the cache is failing.
CACHE_OPERATIONS = frozenset({"get", "set", "delete", "incr", "lock", "other"})

# Boundaries deliberately include 0.2 and 0.45: the SLO is P95 < 200 ms / P99 < 450 ms, and
# `histogram_quantile` interpolates within a bucket, so a quantile is only trustworthy at a
# bucket edge.
HTTP_LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.15,
    0.2,
    0.3,
    0.45,
    0.6,
    1.0,
    2.5,
    5.0,
    10.0,
    float("inf"),
)

# A single query should be an order of magnitude faster than the request that contains it.
DB_LATENCY_BUCKETS = (
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    float("inf"),
)

_LEADING_NOISE = re.compile(r"\A(?:\s+|/\*.*?\*/|--[^\n]*\n?|\()+", re.DOTALL)
_FIRST_WORD = re.compile(r"[A-Za-z]+")

# A CTE is a read in this codebase; bucketing it as `select` keeps read latency in one series.
_VERB_ALIASES = {"with": "select"}


@dataclass(frozen=True)
class Metrics:
    """The collector set, bound to the registry it was built on."""

    registry: CollectorRegistry
    requests: Counter
    request_duration: Histogram
    db_query_duration: Histogram
    cache_hits: Counter
    cache_misses: Counter
    cache_errors: Counter


def build_metrics(registry: CollectorRegistry) -> Metrics:
    """Register the documented collectors on ``registry``."""
    return Metrics(
        registry=registry,
        requests=Counter(
            "http_requests_total",
            "Total HTTP requests handled.",
            ["route", "method", "status"],
            registry=registry,
        ),
        request_duration=Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds.",
            ["route", "method"],
            buckets=HTTP_LATENCY_BUCKETS,
            registry=registry,
        ),
        db_query_duration=Histogram(
            "db_query_duration_seconds",
            "Database statement execution time in seconds.",
            ["query"],
            buckets=DB_LATENCY_BUCKETS,
            registry=registry,
        ),
        cache_hits=Counter(
            "cache_hits_total",
            "Cache reads served from Redis.",
            ["entity"],
            registry=registry,
        ),
        cache_misses=Counter(
            "cache_misses_total",
            "Cache reads that fell through to the database.",
            ["entity"],
            registry=registry,
        ),
        # The degradation counter. This is the series that proves the cache is an
        # optimization and not a dependency: it climbs while requests keep succeeding.
        cache_errors=Counter(
            "cache_errors_total",
            "Cache operations that failed and were degraded around.",
            ["operation"],
            registry=registry,
        ),
    )


# Process-wide collectors. Built once, at import, on the default registry.
METRICS = build_metrics(REGISTRY)


def route_label(scope: Any) -> str:
    """The ``route`` label: the templated path, or the unmatched constant."""
    return route_template(scope)


def sql_operation(statement: Any) -> str:
    """Bucket a SQL statement by its leading verb, ignoring comments and whitespace.

    Anything unrecognised — DDL, transaction control, a driver surprise — becomes ``other``,
    so the label set stays closed no matter what is executed.
    """
    if not isinstance(statement, str):
        return "other"
    stripped = _LEADING_NOISE.sub("", statement)
    match = _FIRST_WORD.match(stripped)
    if match is None:
        return "other"
    verb = match.group().lower()
    verb = _VERB_ALIASES.get(verb, verb)
    return verb if verb in SQL_OPERATIONS else "other"


def observe_request(
    route: str, method: str, status: int, duration_seconds: float, *, metrics: Metrics = METRICS
) -> None:
    """Record one completed request: a counter increment and one latency observation."""
    metrics.requests.labels(route=route, method=method, status=str(status)).inc()
    # Clamp: a non-monotonic clock reading would otherwise poison the histogram sum.
    metrics.request_duration.labels(route=route, method=method).observe(max(duration_seconds, 0.0))


def cache_entity(entity: Any) -> str:
    """Bucket a cache entity into the closed label set."""
    return entity if entity in CACHE_ENTITIES else "other"


def cache_operation(operation: Any) -> str:
    """Bucket a cache operation into the closed label set."""
    return operation if operation in CACHE_OPERATIONS else "other"


def observe_cache_hit(entity: str, *, metrics: Metrics = METRICS) -> None:
    """Record one cache read served from Redis."""
    metrics.cache_hits.labels(entity=cache_entity(entity)).inc()


def observe_cache_miss(entity: str, *, metrics: Metrics = METRICS) -> None:
    """Record one cache read that fell through to the database."""
    metrics.cache_misses.labels(entity=cache_entity(entity)).inc()


def observe_cache_error(operation: str, *, metrics: Metrics = METRICS) -> None:
    """Record one degraded cache operation."""
    metrics.cache_errors.labels(operation=cache_operation(operation)).inc()


def observe_query(statement: Any, duration_seconds: float, *, metrics: Metrics = METRICS) -> None:
    """Record one database statement execution under its verb bucket."""
    metrics.db_query_duration.labels(query=sql_operation(statement)).observe(
        max(duration_seconds, 0.0)
    )


def render_metrics(*, metrics: Metrics = METRICS) -> tuple[bytes, str]:
    """The exposition payload and the content type Prometheus needs to parse it."""
    return generate_latest(metrics.registry), CONTENT_TYPE_LATEST


# The only values the ``state`` label may take.
POOL_STATES = ("in_use", "available", "overflow")


class _PoolCollector(Collector):
    """Reads the connection pool at scrape time.

    Sampling on scrape rather than per request keeps the counters off the hot path: nothing
    reads them between scrapes, so per-request bookkeeping would be work nobody uses.

    The engine is passed as a callable because it is built lazily; resolving it at
    registration would open a pool at import.
    """

    def __init__(self, engine_factory: Any) -> None:
        self._engine_factory = engine_factory

    def describe(self) -> Any:
        """Name the metric without sampling it.

        Required, not optional. ``CollectorRegistry.register`` discovers names by calling
        ``describe()`` and falls back to ``collect()`` when a collector does not define one —
        which samples the pool at registration time, from inside whatever is registering.
        """
        yield GaugeMetricFamily(
            "db_pool_connections", "Database connections by pool state.", labels=["state"]
        )

    def collect(self) -> Any:
        try:
            pool = self._engine_factory().pool
            in_use = pool.checkedout()
            available = pool.checkedin()
            # SQLAlchemy counts overflow from -max_overflow upward, so a negative value means
            # unused slots. Reporting the raw number would graph a healthy pool as negative.
            overflow = max(0, pool.overflow())
        except Exception:  # noqa: BLE001 — a broken pool must not take /metrics down with it
            return

        gauge = GaugeMetricFamily(
            "db_pool_connections",
            "Database connections by pool state.",
            labels=["state"],
        )
        for state, value in zip(POOL_STATES, (in_use, available, overflow), strict=True):
            gauge.add_metric([state], value)
        yield gauge


def register_pool_metrics(registry: CollectorRegistry, engine_factory: Any) -> None:
    """Export ``db_pool_connections{state}`` from ``engine_factory()``'s pool. Idempotent."""
    for collector in list(getattr(registry, "_collector_to_names", {})):
        if isinstance(collector, _PoolCollector):
            return
    registry.register(_PoolCollector(engine_factory))


_QUERY_START_KEY = "_metrics_query_started"
_ATTACHED_FLAG = "_metrics_listeners_attached"


def attach_query_metrics(sync_engine: Any, *, metrics: Metrics = METRICS) -> None:
    """Time every statement the engine executes. Idempotent.

    Hooks the DBAPI-level cursor events rather than ORM events, so it measures what the
    database actually spent, including statements the ORM emits implicitly. On an async
    engine these live on ``.sync_engine``.
    """
    if getattr(sync_engine, _ATTACHED_FLAG, False):
        # Fresh closures every call, so `event.contains` cannot detect the duplicate.
        return

    def before_cursor_execute(
        conn: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: bool
    ) -> None:
        conn.info[_QUERY_START_KEY] = perf_counter()

    def after_cursor_execute(
        conn: Any, cursor: Any, statement: Any, parameters: Any, context: Any, executemany: bool
    ) -> None:
        started = conn.info.pop(_QUERY_START_KEY, None)
        if started is not None:
            observe_query(statement, perf_counter() - started, metrics=metrics)

    event.listen(sync_engine, "before_cursor_execute", before_cursor_execute)
    event.listen(sync_engine, "after_cursor_execute", after_cursor_execute)
    sync_engine.__dict__[_ATTACHED_FLAG] = True
