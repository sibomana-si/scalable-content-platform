"""Health endpoints for Kubernetes probes.

``/health/live``: pure liveness; no dependencies. Also the harness smoke target.
``/health/ready``: readiness; reflects MySQL + Redis health, 503 when a dependency is down.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.deps import SessionDep
from app.cache.client import get_redis

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
async def ready(
    # SessionDep rather than a local Depends(get_session): the transaction scope is declared
    # once, in app/api/deps.py, and must not be able to drift per-router.
    session: SessionDep,
    redis: Annotated[Redis, Depends(get_redis)],
) -> JSONResponse:
    checks: dict[str, str] = {}
    ok = True

    try:
        await session.execute(text("SELECT 1"))
        checks["mysql"] = "ok"
    # readiness must report, not raise, so a broad catch is intentional here
    except Exception as exc:  # noqa: BLE001
        checks["mysql"] = f"error: {exc.__class__.__name__}"
        ok = False

    try:
        await redis.ping()
        checks["redis"] = "ok"
    # readiness must report, not raise, so a broad catch is intentional here
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc.__class__.__name__}"
        ok = False

    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "degraded", "checks": checks},
    )
