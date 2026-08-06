"""Prometheus scrape endpoint.

Unauthenticated by design: Prometheus scrapes without credentials, and the
endpoint is protected at the network layer — it is never routed through the public gateway.
It exposes no request payloads, only aggregate counters and histograms whose label values
come from closed sets.
"""

from fastapi import APIRouter
from fastapi.responses import Response

from app.observability.metrics import METRICS_PATH, render_metrics

router = APIRouter(tags=["observability"])


@router.get(METRICS_PATH, include_in_schema=False)
async def metrics() -> Response:
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)
