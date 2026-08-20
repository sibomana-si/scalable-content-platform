"""Unit tests for the Prometheus instrumentation.

Collectors are built through a ``build_metrics(registry)`` factory so these tests own a
private ``CollectorRegistry``: the process-wide default registry is a global, and counters
that leaked across tests would make assertions order-dependent.
"""

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from app.observability.context import UNMATCHED_ROUTE
from app.observability.metrics import (
    SQL_OPERATIONS,
    Metrics,
    build_metrics,
    observe_cache_error,
    observe_cache_hit,
    observe_cache_miss,
    observe_query,
    observe_request,
    register_pool_metrics,
    render_metrics,
    route_label,
    sql_operation,
)


class _Route:
    def __init__(self, path: str) -> None:
        self.path = path


@pytest.fixture
def metrics() -> Metrics:
    return build_metrics(CollectorRegistry())


def requests(m: Metrics, route: str, method: str, status: str) -> float | None:
    return m.registry.get_sample_value(
        "http_requests_total", {"route": route, "method": method, "status": status}
    )


def latency(m: Metrics, route: str, method: str, suffix: str) -> float | None:
    return m.registry.get_sample_value(
        f"http_request_duration_seconds_{suffix}", {"route": route, "method": method}
    )


def db_latency(m: Metrics, query: str, suffix: str) -> float | None:
    return m.registry.get_sample_value(f"db_query_duration_seconds_{suffix}", {"query": query})


# --- route_label ----------------------------------------------------------------------------


def test_route_label_is_the_templated_path() -> None:
    scope = {"route": _Route("/v1/articles/{article_id}"), "path": "/v1/articles/9001"}

    assert route_label(scope) == "/v1/articles/{article_id}"


def test_route_label_handles_the_root_path() -> None:
    assert route_label({"route": _Route("/")}) == "/"


@pytest.mark.parametrize("scope", [{}, {"route": None}, {"route": object()}])
def test_unmatched_requests_share_one_label(scope: dict) -> None:
    # Without this, `GET /<random>` from a scanner would mint a new time series per request.
    assert route_label(scope) == UNMATCHED_ROUTE


# --- sql_operation --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("SELECT 1", "select"),
        ("select * from articles", "select"),
        ("  \n\t SELECT 1", "select"),  # leading whitespace/newlines
        ("/* hint */ SELECT 1", "select"),  # leading block comment
        ("-- a comment\nSELECT 1", "select"),  # leading line comment
        ("/* a */\n-- b\n  SELECT 1", "select"),  # both, interleaved
        ("WITH recent AS (SELECT 1) SELECT * FROM recent", "select"),  # CTE
        ("INSERT INTO articles (title) VALUES ('x')", "insert"),
        ("UpDaTe articles SET title='x'", "update"),  # mixed case
        ("DELETE FROM articles WHERE id = 1", "delete"),
        ("(SELECT 1)", "select"),  # parenthesised
        ("BEGIN", "other"),
        ("SHOW TABLES", "other"),
        ("", "other"),
        ("   ", "other"),
        ("/* only a comment */", "other"),
    ],
)
def test_sql_operation_buckets_statements_by_verb(statement: str, expected: str) -> None:
    assert sql_operation(statement) == expected


@pytest.mark.parametrize("statement", [None, 42, b"SELECT 1"])
def test_sql_operation_tolerates_non_string_statements(statement: object) -> None:
    # A driver-level surprise must not take down the request it is instrumenting.
    assert sql_operation(statement) == "other"


def test_sql_operation_labels_are_drawn_from_a_closed_set() -> None:
    statements = ["SELECT 1", "INSERT x", "UPDATE x", "DELETE x", "GRANT ALL", "'; DROP TABLE --"]

    assert {sql_operation(s) for s in statements} <= SQL_OPERATIONS


# --- build_metrics --------------------------------------------------------------------------


def test_collectors_carry_the_documented_names_and_labels(metrics: Metrics) -> None:
    names = {m.name for m in metrics.registry.collect()}

    assert "http_requests" in names  # exposed as http_requests_total
    assert "http_request_duration_seconds" in names
    assert "db_query_duration_seconds" in names
    assert metrics.requests._labelnames == ("route", "method", "status")
    assert metrics.request_duration._labelnames == ("route", "method")
    assert metrics.db_query_duration._labelnames == ("query",)


def test_latency_buckets_bracket_the_slo_targets(metrics: Metrics) -> None:
    # The SLO is P95 < 200 ms / P99 < 450 ms; a histogram can only answer that if it has
    # bucket boundaries at those values (histogram_quantile interpolates between edges).
    assert {0.2, 0.45} <= set(metrics.request_duration._upper_bounds)


def test_build_metrics_uses_the_supplied_registry(metrics: Metrics) -> None:
    other = build_metrics(CollectorRegistry())
    observe_request("/health/live", "GET", 200, 0.01, metrics=metrics)

    assert requests(metrics, "/health/live", "GET", "200") == 1.0
    assert requests(other, "/health/live", "GET", "200") is None


# --- observe_request ------------------------------------------------------------------------


def test_observe_request_counts_and_times_one_request(metrics: Metrics) -> None:
    observe_request("/v1/articles", "GET", 200, 0.123, metrics=metrics)

    assert requests(metrics, "/v1/articles", "GET", "200") == 1.0
    assert latency(metrics, "/v1/articles", "GET", "count") == 1.0
    assert latency(metrics, "/v1/articles", "GET", "sum") == pytest.approx(0.123)


def test_status_is_a_string_label_so_promql_can_match_on_it(metrics: Metrics) -> None:
    observe_request("/v1/articles", "GET", 500, 0.01, metrics=metrics)

    # `status=~"5.."` is how the error-rate SLI is written; the label must be the code itself.
    assert requests(metrics, "/v1/articles", "GET", "500") == 1.0


def test_repeated_requests_accumulate_on_one_series(metrics: Metrics) -> None:
    for _ in range(3):
        observe_request("/v1/articles", "GET", 200, 0.01, metrics=metrics)

    assert requests(metrics, "/v1/articles", "GET", "200") == 3.0


def test_statuses_are_separate_series_but_share_a_latency_histogram(metrics: Metrics) -> None:
    observe_request("/v1/articles", "GET", 200, 0.01, metrics=metrics)
    observe_request("/v1/articles", "GET", 404, 0.02, metrics=metrics)

    assert requests(metrics, "/v1/articles", "GET", "200") == 1.0
    assert requests(metrics, "/v1/articles", "GET", "404") == 1.0
    # Latency is not sliced by status: the SLI is "how long did the endpoint take".
    assert latency(metrics, "/v1/articles", "GET", "count") == 2.0


def test_negative_durations_are_clamped(metrics: Metrics) -> None:
    # A non-monotonic clock reading must not poison the histogram sum.
    observe_request("/v1/articles", "GET", 200, -0.5, metrics=metrics)

    assert latency(metrics, "/v1/articles", "GET", "sum") == 0.0


# --- observe_query --------------------------------------------------------------------------


def test_observe_query_records_against_the_verb_bucket(metrics: Metrics) -> None:
    observe_query("SELECT 1", 0.004, metrics=metrics)

    assert db_latency(metrics, "select", "count") == 1.0
    assert db_latency(metrics, "select", "sum") == pytest.approx(0.004)


def test_query_label_never_contains_the_statement_text(metrics: Metrics) -> None:
    # Raw SQL as a label would be unbounded cardinality and would leak literals.
    observe_query("SELECT * FROM users WHERE email = 'secret@example.com'", 0.001, metrics=metrics)
    exposition = render_metrics(metrics=metrics)[0].decode()

    assert "secret@example.com" not in exposition
    assert 'query="select"' in exposition


# --- render_metrics -------------------------------------------------------------------------


def test_render_metrics_returns_exposition_text_and_content_type(metrics: Metrics) -> None:
    observe_request("/health/live", "GET", 200, 0.01, metrics=metrics)
    payload, content_type = render_metrics(metrics=metrics)

    assert isinstance(payload, bytes)
    assert content_type.startswith("text/plain")
    assert "version=" in content_type  # Prometheus needs the format version to parse it
    assert b"# TYPE http_requests_total counter" in payload
    assert b"# TYPE http_request_duration_seconds histogram" in payload


def test_render_metrics_declares_collectors_before_they_are_used(metrics: Metrics) -> None:
    payload, _ = render_metrics(metrics=metrics)

    # HELP/TYPE headers are emitted for declared-but-unobserved collectors, so a scrape is
    # well-formed from the very first one.
    assert b"http_requests_total" in payload


# --- cache metrics --------------------------------------------------------------------------


def cache_counter(m: Metrics, name: str, labels: dict[str, str]) -> float | None:
    return m.registry.get_sample_value(name, labels)


def test_cache_collectors_carry_the_documented_names_and_labels(metrics: Metrics) -> None:
    names = {m.name for m in metrics.registry.collect()}

    assert {"cache_hits", "cache_misses", "cache_errors"} <= names
    assert metrics.cache_hits._labelnames == ("entity",)
    assert metrics.cache_misses._labelnames == ("entity",)
    assert metrics.cache_errors._labelnames == ("operation",)


def test_observe_cache_hit_and_miss_are_separate_series(metrics: Metrics) -> None:
    observe_cache_hit("article", metrics=metrics)
    observe_cache_miss("article", metrics=metrics)
    observe_cache_miss("list", metrics=metrics)

    assert cache_counter(metrics, "cache_hits_total", {"entity": "article"}) == 1.0
    assert cache_counter(metrics, "cache_misses_total", {"entity": "article"}) == 1.0
    assert cache_counter(metrics, "cache_misses_total", {"entity": "list"}) == 1.0


def test_cache_entity_label_is_a_closed_set(metrics: Metrics) -> None:
    # An unbounded entity label is a memory hole in the scrape target; anything unknown
    # must collapse into the closed set rather than mint a new series.
    observe_cache_hit("article:42", metrics=metrics)

    assert cache_counter(metrics, "cache_hits_total", {"entity": "article:42"}) is None
    assert cache_counter(metrics, "cache_hits_total", {"entity": "other"}) == 1.0


def test_cache_operation_label_is_a_closed_set(metrics: Metrics) -> None:
    observe_cache_error("DEL article:42", metrics=metrics)

    assert cache_counter(metrics, "cache_errors_total", {"operation": "other"}) == 1.0


def test_cache_error_counts_under_its_operation(metrics: Metrics) -> None:
    observe_cache_error("get", metrics=metrics)
    observe_cache_error("get", metrics=metrics)
    observe_cache_error("set", metrics=metrics)

    assert cache_counter(metrics, "cache_errors_total", {"operation": "get"}) == 2.0
    assert cache_counter(metrics, "cache_errors_total", {"operation": "set"}) == 1.0


# --- pool metrics ------------------------------------------------------------------------------


class FakePool:
    """Stands in for a SQLAlchemy QueuePool: only the four counters are read."""

    def __init__(self, size=10, checkedin=7, checkedout=3, overflow=-5) -> None:
        self._size, self._in, self._out, self._overflow = size, checkedin, checkedout, overflow

    def size(self) -> int:
        return self._size

    def checkedin(self) -> int:
        return self._in

    def checkedout(self) -> int:
        return self._out

    def overflow(self) -> int:
        return self._overflow


class FakeEngine:
    def __init__(self, pool=None) -> None:
        self.pool = pool or FakePool()


def gauge(registry, state: str) -> float | None:
    return registry.get_sample_value("db_pool_connections", {"state": state})


def test_the_pool_collector_exports_the_three_states() -> None:
    registry = CollectorRegistry()
    register_pool_metrics(registry, lambda: FakeEngine())

    assert gauge(registry, "in_use") == 3.0
    assert gauge(registry, "available") == 7.0
    assert gauge(registry, "overflow") == 0.0  # -5 means five unused overflow slots


def test_the_pool_state_label_is_a_closed_set() -> None:
    registry = CollectorRegistry()
    register_pool_metrics(registry, lambda: FakeEngine())
    states = {
        sample.labels["state"]
        for metric in registry.collect()
        for sample in metric.samples
        if sample.name == "db_pool_connections"
    }

    assert states == {"in_use", "available", "overflow"}


def test_overflow_reports_the_slots_actually_in_use() -> None:
    """SQLAlchemy's overflow() counts from -max_overflow, so a raw read is misleading."""

    registry = CollectorRegistry()
    register_pool_metrics(registry, lambda: FakeEngine(FakePool(overflow=2)))

    assert gauge(registry, "overflow") == 2.0


def test_the_collector_samples_at_scrape_time_not_at_registration() -> None:
    """Sampling per request would put work on the hot path for a number nobody reads between
    scrapes."""

    pool = FakePool(checkedout=1)
    registry = CollectorRegistry()
    register_pool_metrics(registry, lambda: FakeEngine(pool))
    assert gauge(registry, "in_use") == 1.0

    pool._out = 9

    assert gauge(registry, "in_use") == 9.0


def test_an_unreachable_engine_exports_nothing_rather_than_failing_the_scrape() -> None:
    """A broken pool must not take /metrics down with it — that is when it is needed most."""

    def boom():
        raise RuntimeError("no engine")

    registry = CollectorRegistry()
    register_pool_metrics(registry, boom)

    assert gauge(registry, "in_use") is None
    assert generate_latest(registry) is not None


def test_registering_the_collector_never_calls_the_engine_factory() -> None:
    """Registration must not sample.

    ``CollectorRegistry.register`` discovers metric names by calling ``describe()``, or by
    falling back to ``collect()`` when a collector does not define one. That fallback
    deadlocked: ``get_engine`` is ``lru_cache``d and registered the collector from inside its
    own body, so the sample re-entered a function that had not returned yet.
    """

    calls = []

    def factory():
        calls.append(1)
        return FakeEngine()

    register_pool_metrics(CollectorRegistry(), factory)

    assert calls == []


def test_registering_twice_adds_one_collector() -> None:
    """`get_engine` may run again after a dispose; a second registration must not raise."""

    registry = CollectorRegistry()
    register_pool_metrics(registry, lambda: FakeEngine())
    register_pool_metrics(registry, lambda: FakeEngine())

    assert gauge(registry, "in_use") == 3.0
