"""Unit tests for the tracing layer.

Spans are asserted against an ``InMemorySpanExporter``; no collector, no network. The
governing constraint is that telemetry is best-effort: nothing here may change the
outcome of the code it wraps, so the disabled path and the exception path are pinned as hard
as the happy one.
"""

from collections.abc import Iterator

import pytest
import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NoOpTracerProvider, StatusCode

from app.config import Settings
from app.observability.tracing import (
    annotate_current_span,
    build_resource,
    configure_tracing,
    traced,
    tracing_enabled,
)


@pytest.fixture(scope="module")
def exporter() -> Iterator[InMemorySpanExporter]:
    """A real SDK provider feeding an in-memory exporter.

    Installed through ``configure_tracing`` rather than ``set_tracer_provider`` directly:
    the tracer provider is a once-per-process global, so a module that installs its own
    would be silently ignored whenever another test file got there first.
    """
    exporter = InMemorySpanExporter()
    configure_tracing(
        span_processor=SimpleSpanProcessor(exporter),
        settings=Settings(otel_service_name="test-service"),
    )
    yield exporter
    exporter.clear()


@pytest.fixture
def spans(exporter: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    exporter.clear()
    structlog.contextvars.clear_contextvars()
    yield exporter
    structlog.contextvars.clear_contextvars()
    exporter.clear()


@pytest.fixture
def no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the shape of a deployment (and every CI run) with no collector configured."""
    monkeypatch.setattr(trace, "get_tracer_provider", NoOpTracerProvider)


def finished(spans: InMemorySpanExporter) -> list[ReadableSpan]:
    return list(spans.get_finished_spans())


# --- tracing_enabled ------------------------------------------------------------------------


def test_tracing_is_off_when_no_collector_endpoint_is_configured() -> None:
    # The default, and the CI default: no endpoint means no exporter and no outbound
    # connection attempt behind every request.
    assert tracing_enabled(Settings(otel_exporter_otlp_endpoint="")) is False


def test_whitespace_only_endpoint_counts_as_unset() -> None:
    assert tracing_enabled(Settings(otel_exporter_otlp_endpoint="   ")) is False


def test_tracing_is_on_once_an_endpoint_is_configured() -> None:
    assert tracing_enabled(Settings(otel_exporter_otlp_endpoint="http://otel:4318")) is True


# --- build_resource -------------------------------------------------------------------------


def test_resource_names_the_service() -> None:
    resource = build_resource(Settings(otel_service_name="content-platform"))

    # Without this every trace lands in the collector as "unknown_service".
    assert resource.attributes["service.name"] == "content-platform"
    assert resource.attributes["service.version"]


# --- configure_tracing ----------------------------------------------------------------------


class _App:
    """Stands in for a FastAPI app; only the instrumentation marker matters here."""


def test_configure_tracing_is_a_no_op_when_disabled() -> None:
    app = _App()

    installed = configure_tracing(app, settings=Settings(otel_exporter_otlp_endpoint=""))

    assert installed is False
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)


def test_configure_tracing_installs_when_a_processor_is_supplied(
    exporter: InMemorySpanExporter,
) -> None:
    installed = configure_tracing(
        settings=Settings(otel_exporter_otlp_endpoint=""),
        span_processor=SimpleSpanProcessor(InMemorySpanExporter()),
    )

    assert installed is True
    assert isinstance(trace.get_tracer_provider(), TracerProvider)


def test_configure_tracing_is_idempotent(exporter: InMemorySpanExporter) -> None:
    settings = Settings(otel_exporter_otlp_endpoint="http://otel:4318")

    first = configure_tracing(
        settings=settings, span_processor=SimpleSpanProcessor(InMemorySpanExporter())
    )
    second = configure_tracing(
        settings=settings, span_processor=SimpleSpanProcessor(InMemorySpanExporter())
    )
    provider = trace.get_tracer_provider()

    assert (first, second) == (True, True)
    # One provider, reused, set_tracer_provider is a once-per-process operation.
    assert provider is trace.get_tracer_provider()


# --- traced ---------------------------------------------------------------------------------


async def test_traced_emits_one_span_named_component_dot_operation(
    spans: InMemorySpanExporter,
) -> None:
    async with traced("articles", "get"):
        pass

    assert [s.name for s in finished(spans)] == ["articles.get"]


async def test_traced_attaches_supplied_attributes(spans: InMemorySpanExporter) -> None:
    async with traced("articles", "get", article_id=42):
        pass

    assert finished(spans)[0].attributes["article_id"] == 42  # type: ignore[index]


async def test_traced_attaches_the_request_id_from_the_log_context(
    spans: InMemorySpanExporter,
) -> None:
    structlog.contextvars.bind_contextvars(request_id="rid-42")

    async with traced("articles", "get"):
        pass

    # This is the logs -> traces join: the same id is on the span and in every log line.
    assert finished(spans)[0].attributes["request_id"] == "rid-42"  # type: ignore[index]


async def test_traced_omits_request_id_when_there_is_none(spans: InMemorySpanExporter) -> None:
    async with traced("articles", "get"):
        pass

    assert "request_id" not in finished(spans)[0].attributes  # type: ignore[operator]


async def test_traced_records_the_exception_and_marks_the_span_failed(
    spans: InMemorySpanExporter,
) -> None:
    with pytest.raises(ValueError, match="boom"):
        async with traced("articles", "update"):
            raise ValueError("boom")

    span = finished(spans)[0]
    assert span.status.status_code is StatusCode.ERROR
    assert [event.name for event in span.events] == ["exception"]


async def test_traced_nests_spans_under_their_caller(spans: InMemorySpanExporter) -> None:
    async with traced("articles", "update"), traced("articles", "get"):
        pass

    inner, outer = finished(spans)  # children finish first
    assert (inner.name, outer.name) == ("articles.get", "articles.update")
    assert inner.parent.span_id == outer.context.span_id  # type: ignore[union-attr]


async def test_traced_yields_the_span_for_late_annotation(spans: InMemorySpanExporter) -> None:
    async with traced("articles", "list") as span:
        span.set_attribute("page_size", 20)

    assert finished(spans)[0].attributes["page_size"] == 20  # type: ignore[index]


# --- the disabled path: no provider, no behaviour change ------------------------------------


async def test_traced_still_runs_the_body_with_no_provider_installed(no_provider: None) -> None:
    executed = []

    async with traced("articles", "get") as span:
        span.set_attribute("ignored", 1)  # must be tolerated, not raise
        executed.append("body")

    assert executed == ["body"]


async def test_traced_propagates_exceptions_with_no_provider_installed(no_provider: None) -> None:
    # Instrumentation must never change control flow.
    with pytest.raises(RuntimeError, match="still raised"):
        async with traced("articles", "get"):
            raise RuntimeError("still raised")


# --- annotate_current_span ------------------------------------------------------------------


def test_annotate_current_span_returns_none_outside_a_span() -> None:
    # No active server span (tracing off): callers must cope with there being no trace id.
    assert annotate_current_span("rid-1") is None


def test_annotate_current_span_returns_the_trace_id_and_tags_the_span(
    spans: InMemorySpanExporter,
) -> None:
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("http.server"):
        trace_id = annotate_current_span("rid-1")

    assert trace_id is not None
    assert len(trace_id) == 32
    assert int(trace_id, 16) != 0
    assert finished(spans)[0].attributes["request_id"] == "rid-1"  # type: ignore[index]
