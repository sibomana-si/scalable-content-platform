"""End-to-end trace shape against a real database.

The NFR asks for "traces with DB and cache spans", which is only meaningful once the SQL is
real: this asserts the nesting HTTP server span → ``articles.*`` domain span → SQLAlchemy DB
span, i.e. that context actually propagates from the ASGI layer down through the service into
the driver.
"""

from collections.abc import AsyncGenerator, Iterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.main import create_app
from app.observability.tracing import configure_tracing

pytestmark = pytest.mark.integration


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    yield exporter
    exporter.clear()


@pytest.fixture
def app(exporter: InMemorySpanExporter) -> FastAPI:
    app = create_app()
    configure_tracing(app, span_processor=SimpleSpanProcessor(exporter))
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def by_name(exporter: InMemorySpanExporter) -> dict[str, ReadableSpan]:
    return {span.name: span for span in exporter.get_finished_spans()}


def child_spans(
    exporter: InMemorySpanExporter, parent: ReadableSpan, system: str
) -> list[ReadableSpan]:
    """Statement spans of one backend issued by ``parent``.

    Filtering on the parent matters: the test fixtures also clean the tables through the same
    instrumented engine, and the pool's ``connect`` span carries ``db.system`` with no
    statement of its own. Filtering on ``db.system`` matters too — the read path is
    cache-aside, so a domain span now has Redis children as well as MySQL ones.
    """
    return [
        span
        for span in exporter.get_finished_spans()
        if (span.attributes or {}).get("db.statement")
        and (span.attributes or {}).get("db.system") == system
        and span.parent is not None
        and span.parent.span_id == parent.context.span_id
    ]


def db_span(exporter: InMemorySpanExporter, parent: ReadableSpan) -> ReadableSpan:
    """The MySQL statement span issued by ``parent``."""
    return child_spans(exporter, parent, "mysql")[0]


def cache_span(exporter: InMemorySpanExporter, parent: ReadableSpan) -> ReadableSpan:
    """The Redis command span issued by ``parent``."""
    return child_spans(exporter, parent, "redis")[0]


async def test_a_read_produces_a_server_domain_and_db_span(
    client: AsyncClient, clean_db: None, exporter: InMemorySpanExporter
) -> None:
    response = await client.get("/v1/articles")
    assert response.status_code == 200

    spans = by_name(exporter)

    assert "GET /v1/articles" in spans  # auto-instrumented HTTP server span
    assert "articles.list" in spans  # domain span, <component>.<operation>
    assert db_span(exporter, spans["articles.list"]).attributes["db.system"] == "mysql"  # type: ignore[index]
    # The NFR asks for DB and cache spans; a cache-aside read produces both.
    assert cache_span(exporter, spans["articles.list"]).attributes["db.system"] == "redis"  # type: ignore[index]


async def test_the_spans_nest_into_a_single_trace(
    client: AsyncClient, clean_db: None, exporter: InMemorySpanExporter
) -> None:
    await client.get("/v1/articles")

    spans = by_name(exporter)
    server, domain = spans["GET /v1/articles"], spans["articles.list"]
    db = db_span(exporter, domain)

    assert domain.parent.span_id == server.context.span_id  # type: ignore[union-attr]
    # One trace end to end: this is what makes a slow endpoint attributable to a slow query.
    assert {s.context.trace_id for s in (server, domain, db)} == {server.context.trace_id}


async def test_a_cache_hit_produces_no_database_span(
    client: AsyncClient, clean_db: None, clean_cache: None, exporter: InMemorySpanExporter
) -> None:
    """What the cache is for, read off the trace: the second request never reaches MySQL."""
    await client.get("/v1/articles?limit=5")
    exporter.clear()

    await client.get("/v1/articles?limit=5")

    domain = by_name(exporter)["articles.list"]
    assert child_spans(exporter, domain, "mysql") == []
    assert child_spans(exporter, domain, "redis")


async def test_the_db_span_carries_parameterised_sql_not_literals(
    client: AsyncClient, clean_db: None, clean_cache: None, exporter: InMemorySpanExporter
) -> None:
    await client.get("/v1/articles?limit=5")

    statement = db_span(exporter, by_name(exporter)["articles.list"]).attributes["db.statement"]  # type: ignore[index]

    # Bound parameters stay placeholders, so a trace backend never accumulates row data.
    assert "FROM articles" in statement  # type: ignore[operator]
    assert "LIMIT %s" in statement  # type: ignore[operator]


async def test_a_write_carries_the_request_id_all_the_way_down(
    client: AsyncClient, clean_db: None, exporter: InMemorySpanExporter
) -> None:
    await client.get("/v1/articles", headers={"X-Request-ID": "join-me"})

    spans = by_name(exporter)

    assert spans["GET /v1/articles"].attributes["request_id"] == "join-me"  # type: ignore[index]
    assert spans["articles.list"].attributes["request_id"] == "join-me"  # type: ignore[index]


async def test_a_domain_failure_marks_its_span_without_breaking_the_response(
    client: AsyncClient, clean_db: None, exporter: InMemorySpanExporter
) -> None:
    response = await client.get("/v1/articles/999999")
    assert response.status_code == 404

    span = by_name(exporter)["articles.get"]

    # The 404 is a domain outcome, and the span records it — but the client still gets the
    # canonical envelope, unchanged by instrumentation.
    assert span.status.status_code.name == "ERROR"
    assert response.json()["error"]["code"] == "ARTICLE_NOT_FOUND"
