from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import (
    AccessLogMiddleware,
    AuthMiddleware,
    MetricsMiddleware,
    RequestIDMiddleware,
)
from app.api.routers import articles, auth, health, metrics
from app.observability.logging import configure_logging

# Configure logging at import, not in a lifespan hook: app construction itself should be
# logged in the production format, and the ASGI test harness never runs lifespan events.
configure_logging()


def create_app() -> FastAPI:
    app = FastAPI(title="Scalable Content Platform Backend", version=__version__)
    register_exception_handlers(app)
    # Starlette wraps in reverse: the last added middleware is the outermost layer, so this
    # reads inside-out and yields the documented chain
    # request-id → access log → RED metrics → auth/RBAC.
    app.add_middleware(AuthMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(auth.router)
    app.include_router(articles.router)
    return app


app = create_app()
