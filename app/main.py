from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.routers import articles, health


def create_app() -> FastAPI:
    app = FastAPI(title="Scalable Content Platform Backend", version=__version__)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(articles.router)
    return app


app = create_app()
