"""Unit tests for the Prometheus instrumentation.

Collectors are built through a ``build_metrics(registry)`` factory so these tests own a
private ``CollectorRegistry``: the process-wide default registry is a global, and counters
that leaked across tests would make assertions order-dependent.
"""

import pytest
from prometheus_client import CollectorRegistry

from app.observability.context import UNMATCHED_ROUTE
from app.observability.metrics import (
    SQL_OPERATIONS,
    Metrics,
    build_metrics,
    observe_query,
    observe_request,
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
