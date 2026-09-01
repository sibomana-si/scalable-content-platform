from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import (
    AccessLogMiddleware,
    AuthMiddleware,
    LoadShedMiddleware,
    MetricsMiddleware,
    RequestIDMiddleware,
)
from app.api.routers import articles, auth, health, metrics
from app.config import get_settings
from app.observability.logging import configure_logging
from app.observability.tracing import configure_tracing

# Configure logging at import, not in a lifespan hook: app construction itself should be
# logged in the production format, and the ASGI test harness never runs lifespan events.
configure_logging()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Scalable Content Platform Backend", version=__version__)
    register_exception_handlers(app)
    # Starlette wraps in reverse: the last added middleware is the outermost layer, so this
    # reads inside-out and yields the documented chain
    # request-id → access log → RED metrics → load shedding → auth/RBAC.
    app.add_middleware(AuthMiddleware)
    # Inside the metrics layer, so a shed request still counts in http_requests_total and in
    # the access log; outside authentication, so a refusal costs no token verification.
    app.add_middleware(
        LoadShedMiddleware,
        max_inflight=settings.max_inflight_requests,
        retry_after_seconds=settings.shed_retry_after_seconds,
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(auth.router)
    app.include_router(articles.router)
    # Last, so the OTel server span wraps every layer above and the request-id middleware can
    # annotate it. A no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is configured.
    configure_tracing(app)
    return app


app = create_app()
