"""OpenTelemetry tracing.

Two layers of span names coexist, deliberately:

* **Auto-instrumentation** (FastAPI, SQLAlchemy, redis) emits spans under the OTel
  semantic conventions — ``GET /v1/articles/{article_id}``, ``SELECT content_platform``,
  ``GET`` for Redis. These are what every OTel-aware backend already knows how to read.
* **Domain spans** wrap service-layer operations under the project's own
  ``<component>.<operation>`` convention (``articles.get``, ``auth.login``) via
  :func:`traced`, so a trace shows what the system was doing, not only which library it
  was in.

Export is best-effort: a missing or broken collector must never fail or slow a request.
Tracing therefore stays entirely off unless ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set — which is
 also what keeps CI offline — and :func:`traced` degrades to a no-op context manager rather
 than raising when no provider is installed.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

from app import __version__
from app.config import Settings, get_settings

REQUEST_ID_ATTRIBUTE = "request_id"

# Module-level latches: the app factory runs once per test, and neither the OTLP exporter nor
# the library instrumentors may be installed twice in a process.
_INSTALLED = {"exporter": False, "libraries": False}


def tracing_enabled(settings: Settings | None = None) -> bool:
    """Whether to install tracing at all, i.e. whether a collector endpoint is configured."""
    settings = settings or get_settings()
    return bool(settings.otel_exporter_otlp_endpoint.strip())


def build_resource(settings: Settings | None = None) -> Resource:
    """Identify this service to the collector.

    Without ``service.name`` every trace arrives attributed to ``unknown_service``, which
    makes a shared collector useless.
    """
    settings = settings or get_settings()
    return Resource.create({SERVICE_NAME: settings.otel_service_name, SERVICE_VERSION: __version__})


def _sdk_provider(settings: Settings) -> TracerProvider:
    """The process's SDK tracer provider, created on first use.

    ``set_tracer_provider`` is a once-per-process operation, so an already-installed SDK
    provider is reused rather than fought with.
    """
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return current
    provider = TracerProvider(resource=build_resource(settings))
    trace.set_tracer_provider(provider)
    return provider


def configure_tracing(
    app: Any = None,
    *,
    span_processor: SpanProcessor | None = None,
    settings: Settings | None = None,
) -> bool:
    """Install tracing. Returns whether it was installed; idempotent.

    ``span_processor`` is the test seam: supplying one both bypasses the endpoint check and
    replaces the OTLP exporter, so tests can assert on spans without a collector.
    """
    settings = settings or get_settings()
    if span_processor is None and not tracing_enabled(settings):
        return False

    provider = _sdk_provider(settings)

    if span_processor is not None:
        provider.add_span_processor(span_processor)
    elif not _INSTALLED["exporter"]:
        # Batched and asynchronous: a slow or dead collector must not enter the request path.
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint.strip())
            )
        )
        _INSTALLED["exporter"] = True

    if not _INSTALLED["libraries"]:
        from app.db.session import get_engine

        SQLAlchemyInstrumentor().instrument(
            engine=get_engine().sync_engine, tracer_provider=provider
        )
        RedisInstrumentor().instrument(tracer_provider=provider)
        _INSTALLED["libraries"] = True

    # Instrumenting the same app twice would emit two server spans per request.
    if app is not None and not getattr(app, "_is_instrumented_by_opentelemetry", False):
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

    return True


@asynccontextmanager
async def traced(component: str, operation: str, **attributes: Any) -> AsyncIterator[Any]:
    """A domain span named ``<component>.<operation>``.

    Carries the ``request_id`` from the log context, so a log line and its span can be joined
    from either direction. Exception recording and the ERROR status come from the SDK's own
    ``start_as_current_span`` semantics; the exception is always re-raised — instrumentation
    must not alter control flow.

    With no provider installed this resolves to a no-op tracer and the body simply runs.
    """
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(f"{component}.{operation}") as span:
        request_id = structlog.contextvars.get_contextvars().get(REQUEST_ID_ATTRIBUTE)
        if request_id:
            span.set_attribute(REQUEST_ID_ATTRIBUTE, request_id)
        for key, value in attributes.items():
            span.set_attribute(key, value)
        yield span


def annotate_current_span(request_id: str) -> str | None:
    """Tag the active span with the request id and return its trace id, or ``None``.

    ``None`` is the normal case when tracing is off — the caller must not assume a trace
    exists.
    """
    span = trace.get_current_span()
    context = span.get_span_context()
    if not context.is_valid:
        return None
    span.set_attribute(REQUEST_ID_ATTRIBUTE, request_id)
    return format(context.trace_id, "032x")
