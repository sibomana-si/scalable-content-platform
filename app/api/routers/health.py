"""Health endpoints for Kubernetes probes.

``/health/live``: pure liveness; no dependencies. Also the harness smoke target.
``/health/ready``: readiness; reports MySQL and Redis, and 503 only when MySQL is down.

Readiness answers one question: should this instance keep receiving traffic? Only MySQL
decides it. ADR-0004 makes the cache an optimization, so an instance with a dead cache serves
every read from MySQL and belongs in the pool. A probe that failed on Redis would remove every
replica at once during a cache outage, which turns a slow service into no service. The Redis
verdict stays in the body, where an operator reads it and a load balancer does not.

Readiness deliberately borrows nothing from the request path. It does not take ``SessionDep``:
that dependency holds its connection until the end of the request, which would keep a pooled
connection checked out across the Redis probe. A cache that hangs rather than fails would then
pin one connection per probe — and because Kubernetes retries on a timer while uvicorn does not
cancel the handler on client disconnect, those accumulate until the pool is gone and real
traffic blocks for ``pool_timeout``. A readiness endpoint must not be able to cause the outage
it is meant to report.
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.cache.client import get_redis
from app.config import get_settings
from app.db.session import get_engine

router = APIRouter(prefix="/health", tags=["health"])

# Identifies the replica answering the probe. Generated once at import, so it is stable for
# the life of the process — a fresh value per request would make one replica look like many.
# Kubernetes and Docker Compose both set HOSTNAME to the container name, which is the useful
# answer when it exists; the uuid is the fallback for a local uvicorn.
INSTANCE_ID = os.environ.get("HOSTNAME") or uuid.uuid4().hex[:12]


@router.get("/live")
async def live() -> dict[str, str]:
    """Liveness, plus which replica answered.

    The instance id is on the probe and nowhere else. Putting it on the data path would leak
    infrastructure detail to every client to serve a need only an operator and the
    multi-replica test have.
    """
    return {"status": "alive", "instance": INSTANCE_ID}


async def _probe_mysql() -> None:
    """Round-trip one statement, holding the connection for no longer than that.

    AUTOCOMMIT because a liveness question has no business opening a transaction or a read
    view. (It does not save a round trip — the pool still resets the connection on return —
    the point is the scope of the checkout, not its cost.)
    """

    async with get_engine().connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("SELECT 1"))


async def _check(probe: Callable[[], Awaitable[object]], timeout: float) -> str:
    """Run one dependency check and describe the outcome; never raise.

    Readiness reports, so every failure becomes a string rather than a 500 — including the
    timeout, which is the case that matters: a dependency that hangs is strictly worse than
    one that refuses, because nothing downstream ever gets told.
    """
    try:
        async with asyncio.timeout(timeout):
            await probe()
    except Exception as exc:  # noqa: BLE001 — the verdict is the return value, not an exception
        return f"error: {exc.__class__.__name__}"
    return "ok"


@router.get("/ready")
async def ready(redis: Annotated[Redis, Depends(get_redis)]) -> JSONResponse:
    timeout = get_settings().readiness_timeout_seconds

    # Sequential, and each check owns its resources for its own duration only: the MySQL
    # connection is back in the pool before Redis is touched.
    checks = {
        "mysql": await _check(_probe_mysql, timeout),
        "redis": await _check(redis.ping, timeout),
    }
    # Three outcomes, not two: serving fully, serving with less, and not serving.
    if checks["mysql"] != "ok":
        return JSONResponse(status_code=503, content={"status": "unready", "checks": checks})
    degraded = any(status != "ok" for status in checks.values())
    return JSONResponse(
        status_code=200,
        content={"status": "degraded" if degraded else "ready", "checks": checks},
    )
