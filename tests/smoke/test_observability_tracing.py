"""Wiring checks for tracing at the HTTP layer: server spans and the logs↔traces join.

No containers: everything here is satisfied by ``/health/live``.
"""

import io
import json
from collections.abc import AsyncGenerator, Iterator

import pytest
import pytest_asyncio
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.main import create_app
from app.observability.context import REQUEST_ID_HEADER
from app.observability.logging import configure_logging
from app.observability.tracing import configure_tracing


@pytest.fixture
def logs() -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging(level="INFO", fmt="json", stream=buffer)
    yield buffer
    structlog.reset_defaults()
    configure_logging()


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    yield exporter
    exporter.clear()


@pytest.fixture
def app(exporter: InMemorySpanExporter) -> FastAPI:
    app = create_app()
    # `span_processor` is the seam that turns tracing on without a collector; create_app()
    # itself leaves it off because no OTEL_EXPORTER_OTLP_ENDPOINT is configured under test.
    configure_tracing(app, span_processor=SimpleSpanProcessor(exporter))
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def server_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    """Distinct SERVER spans. De-duplicated by span id, because a test may deliberately
    attach two processors to the same provider and see each span exported twice."""
    seen: dict[int, ReadableSpan] = {}
    for span in exporter.get_finished_spans():
        if span.kind.name == "SERVER":
            seen.setdefault(span.context.span_id, span)
    return list(seen.values())


def status_of(span: ReadableSpan) -> int | None:
    # The attribute was renamed between OTel semantic-convention versions.
    attributes = span.attributes or {}
    return attributes.get("http.response.status_code", attributes.get("http.status_code"))  # type: ignore[return-value]


async def test_a_request_produces_one_server_span_named_by_its_route(
    client: AsyncClient, exporter: InMemorySpanExporter
) -> None:
    await client.get("/health/live")
    spans = server_spans(exporter)

    assert len(spans) == 1
    assert spans[0].name == "GET /health/live"  # OTel semantic convention: templated route
    assert status_of(spans[0]) == 200


async def test_configuring_twice_does_not_double_instrument_the_app(
    app: FastAPI, client: AsyncClient, exporter: InMemorySpanExporter
) -> None:
    configure_tracing(app, span_processor=SimpleSpanProcessor(exporter))
    await client.get("/health/live")

    # A second FastAPIInstrumentor pass would nest a duplicate server span under the first
    # and double every latency figure derived from traces.
    assert len(server_spans(exporter)) == 1


async def test_the_server_span_carries_the_request_id(
    client: AsyncClient, exporter: InMemorySpanExporter
) -> None:
    await client.get("/health/live", headers={REQUEST_ID_HEADER: "trace-join-1"})

    assert server_spans(exporter)[0].attributes["request_id"] == "trace-join-1"  # type: ignore[index]


async def test_the_access_log_carries_the_trace_id(
    client: AsyncClient, exporter: InMemorySpanExporter, logs: io.StringIO
) -> None:
    await client.get("/health/live")
    records = [json.loads(line) for line in logs.getvalue().splitlines() if line.strip()]
    access = next(r for r in records if r["message"] == "http.request")
    span_trace_id = format(server_spans(exporter)[0].context.trace_id, "032x")

    # The join that makes "show me the trace for this log line" a single click.
    assert access["trace_id"] == span_trace_id


async def test_requests_are_untraced_but_still_logged_when_tracing_is_off(
    logs: io.StringIO,
) -> None:
    untraced = create_app()  # no configure_tracing: the production default under test/CI
    transport = ASGITransport(app=untraced)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health/live")

    records = [json.loads(line) for line in logs.getvalue().splitlines() if line.strip()]
    access = next(r for r in records if r["message"] == "http.request")

    assert response.status_code == 200
    assert "trace_id" not in access  # absent, not null or a zeroed placeholder
