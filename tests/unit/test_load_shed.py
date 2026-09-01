"""The bound that rejects instead of queueing.

Finding F5 of the M6 load test is the reason this middleware exists: past the knee, requests
were lost at the connection level rather than refused. The client saw a dropped iteration, the
server recorded no 5xx, and nothing anywhere said "too much". An instance that cannot serve a
request must say so, fast, with a status a client can act on.

The ceiling is per instance and comes from a measurement, so the tests here pin the behavior
around it rather than the number itself. `test_capacity_model.py` pins the number.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from prometheus_client import CollectorRegistry
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.api.middleware import LoadShedMiddleware, RequestIDMiddleware
from app.observability.metrics import Metrics, build_metrics


@pytest.fixture
def metrics() -> Metrics:
    return build_metrics(CollectorRegistry())


def sample(metrics: Metrics, name: str, **labels: str) -> float:
    value = metrics.registry.get_sample_value(name, labels or None)
    return 0.0 if value is None else value


class Handler:
    """A route that blocks until it is released, so concurrency is controllable."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def handle(self, request) -> PlainTextResponse:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return PlainTextResponse("ok")


def build(handler: Handler, metrics: Metrics, *, max_inflight: int = 1) -> Starlette:
    app = Starlette(
        routes=[
            Route("/slow", handler.handle),
            Route("/health/live", lambda request: PlainTextResponse("alive")),
            Route("/health/ready", lambda request: PlainTextResponse("ready")),
            Route("/metrics", lambda request: PlainTextResponse("# metrics")),
        ]
    )
    app.add_middleware(
        LoadShedMiddleware,
        max_inflight=max_inflight,
        retry_after_seconds=1.0,
        metrics=metrics,
    )
    app.add_middleware(RequestIDMiddleware)
    return app


def client_for(app: Starlette) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_a_request_under_the_ceiling_is_served(metrics: Metrics) -> None:
    handler = Handler()
    handler.release.set()

    async with client_for(build(handler, metrics, max_inflight=2)) as client:
        response = await client.get("/slow")

    assert response.status_code == 200
    assert handler.calls == 1


async def test_the_request_over_the_ceiling_is_shed(metrics: Metrics) -> None:
    """Refused, not queued. A queued request still holds memory and still disappoints later."""

    handler = Handler()
    app = build(handler, metrics, max_inflight=1)

    async with client_for(app) as client:
        held = asyncio.create_task(client.get("/slow"))
        await handler.entered.wait()

        shed = await client.get("/slow")

        handler.release.set()
        assert (await held).status_code == 200

    assert shed.status_code == 503
    assert shed.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    # The handler ran once, for the request that was admitted.
    assert handler.calls == 1


async def test_a_shed_request_tells_the_caller_when_to_return(metrics: Metrics) -> None:
    handler = Handler()
    app = build(handler, metrics, max_inflight=1)

    async with client_for(app) as client:
        held = asyncio.create_task(client.get("/slow"))
        await handler.entered.wait()

        shed = await client.get("/slow")

        handler.release.set()
        await held

    assert shed.headers["Retry-After"] == "1"


async def test_a_shed_request_carries_its_correlation_id(metrics: Metrics) -> None:
    """A caller reporting a 503 must be able to name the request that got it."""

    handler = Handler()
    app = build(handler, metrics, max_inflight=1)

    async with client_for(app) as client:
        held = asyncio.create_task(client.get("/slow"))
        await handler.entered.wait()

        shed = await client.get("/slow")

        handler.release.set()
        await held

    assert shed.headers["X-Request-ID"] == shed.json()["error"]["request_id"]


async def test_the_gauge_returns_to_zero(metrics: Metrics) -> None:
    """A leaked slot is worse than no ceiling: the instance sheds everything, for good."""

    handler = Handler()
    handler.release.set()

    async with client_for(build(handler, metrics, max_inflight=2)) as client:
        await client.get("/slow")
        await client.get("/slow")

    assert sample(metrics, "inflight_requests") == 0.0


async def test_the_gauge_returns_to_zero_after_a_handler_raises(metrics: Metrics) -> None:
    async def boom(request):
        raise RuntimeError("handler failed")

    app = Starlette(routes=[Route("/boom", boom)])
    app.add_middleware(LoadShedMiddleware, max_inflight=1, retry_after_seconds=1.0, metrics=metrics)

    async with client_for(app) as client:
        with pytest.raises(RuntimeError):
            await client.get("/boom")

    assert sample(metrics, "inflight_requests") == 0.0


async def test_the_gauge_reports_the_requests_in_flight(metrics: Metrics) -> None:
    handler = Handler()
    app = build(handler, metrics, max_inflight=2)

    async with client_for(app) as client:
        held = asyncio.create_task(client.get("/slow"))
        await handler.entered.wait()

        assert sample(metrics, "inflight_requests") == 1.0

        handler.release.set()
        await held


async def test_a_shed_request_counts_as_a_degraded_response(metrics: Metrics) -> None:
    handler = Handler()
    app = build(handler, metrics, max_inflight=1)

    async with client_for(app) as client:
        held = asyncio.create_task(client.get("/slow"))
        await handler.entered.wait()

        await client.get("/slow")

        handler.release.set()
        await held

    # The route label is real even though the router never ran: `route_label` resolves the
    # template from the routing table, so a shed request is attributable to its endpoint.
    assert sample(metrics, "degraded_responses_total", route="/slow", reason="load_shed") == 1.0


@pytest.mark.parametrize("path", ["/health/live", "/health/ready", "/metrics"])
async def test_the_probes_and_the_scrape_are_never_shed(metrics: Metrics, path: str) -> None:
    """Shedding a probe turns a busy instance into a restarted one, and hides the evidence.

    Kubernetes reads a 503 on readiness as "take it out of the load balancer", which is right;
    a 503 on liveness is a kill. Prometheus reads a shed scrape as a gap over exactly the
    window an operator needs. Neither costs the instance any real work, so neither takes a slot.
    """

    handler = Handler()
    app = build(handler, metrics, max_inflight=1)

    async with client_for(app) as client:
        held = asyncio.create_task(client.get("/slow"))
        await handler.entered.wait()

        response = await client.get(path)

        handler.release.set()
        await held

    assert response.status_code == 200


async def test_a_ceiling_below_one_is_refused() -> None:
    """A zero ceiling sheds every request, which is an outage spelled as a configuration."""

    with pytest.raises(ValueError, match="MAX_INFLIGHT_REQUESTS"):
        LoadShedMiddleware(None, max_inflight=0, retry_after_seconds=1.0)


async def test_a_non_positive_retry_after_is_refused() -> None:
    with pytest.raises(ValueError, match="SHED_RETRY_AFTER_SECONDS"):
        LoadShedMiddleware(None, max_inflight=10, retry_after_seconds=0.0)


# --- wiring -------------------------------------------------------------------------------


def test_the_shed_layer_sits_inside_the_metrics_layer_and_outside_auth() -> None:
    """Position is the design, and it is not visible from either neighbour.

    Inside the metrics layer, a shed response still counts in `http_requests_total` and still
    writes an access-log line — without that, shedding would look like the silent connection
    loss it was built to replace. Outside authentication, a refusal costs no token
    verification, because the cheapest possible refusal is the point.
    """

    from app.main import create_app

    # `user_middleware` reads outside in: the first entry is the outermost layer.
    chain = [entry.cls.__name__ for entry in create_app().user_middleware]  # type: ignore[attr-defined]

    assert chain.index("MetricsMiddleware") < chain.index("LoadShedMiddleware")
    assert chain.index("LoadShedMiddleware") < chain.index("AuthMiddleware")


def test_the_configured_ceiling_reaches_the_middleware() -> None:
    """A setting nothing reads is a setting that lies."""

    from app.config import get_settings
    from app.main import create_app

    entry = next(e for e in create_app().user_middleware if e.cls.__name__ == "LoadShedMiddleware")  # type: ignore[attr-defined]

    assert entry.kwargs["max_inflight"] == get_settings().max_inflight_requests
    assert entry.kwargs["retry_after_seconds"] == get_settings().shed_retry_after_seconds
