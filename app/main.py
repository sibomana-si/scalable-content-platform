from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import AuthMiddleware
from app.api.routers import articles, auth, health


def create_app() -> FastAPI:
    app = FastAPI(title="Scalable Content Platform Backend", version=__version__)
    register_exception_handlers(app)
    app.add_middleware(AuthMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(articles.router)
    return app


app = create_app()
