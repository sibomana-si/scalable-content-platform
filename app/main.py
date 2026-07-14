from fastapi import FastAPI

from app import __version__
from app.api.routers import health


def create_app() -> FastAPI:
    app = FastAPI(title="Scalable Content Platform Backend", version=__version__)
    app.include_router(health.router)
    return app


app = create_app()
