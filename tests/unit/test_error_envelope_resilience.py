"""The two envelopes a bounded dependency call can produce: 504 and 503.

Commit 2 gave every dependency call a ceiling. Reaching one raises `UpstreamTimeoutError` or
`DependencyUnavailableError`, and without a mapping both leave as a 500 — the status that says
"the server has a bug" for the one case where the server is working exactly as designed.

The `Retry-After` header is the payload here. A client that backs off is the difference between
a dependency that recovers and one that is held down by the callers waiting for it.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY

from app.services.exceptions import (
    ArticleNotFoundError,
    DependencyUnavailableError,
    UpstreamTimeoutError,
)


@pytest.fixture
def failing_app(app: FastAPI) -> FastAPI:
    """The real app, plus two routes that raise what a guarded dependency call raises.

    The routes sit under ``/health`` because that prefix is public, so the envelope under test
    is the one the handler produced and not a 401 from the auth middleware. They are outside
    ``PROBE_PATHS``, so they keep the metrics and access-log layers a real request goes through.
    """

    @app.get("/health/testing/timeout")
    async def _timeout() -> None:
        raise UpstreamTimeoutError("mysql did not answer in time", retry_after=3.0)

    @app.get("/health/testing/unavailable")
    async def _unavailable() -> None:
        raise DependencyUnavailableError("mysql is unavailable", retry_after=10.0)

    return app


@pytest.fixture
async def failing_client(failing_app: FastAPI):
    transport = ASGITransport(app=failing_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_a_dependency_timeout_is_a_504(failing_client: AsyncClient) -> None:
    """504, not 500. The request failed upstream of the handler, and a retry may work."""

    response = await failing_client.get("/health/testing/timeout")

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "UPSTREAM_TIMEOUT"


async def test_an_open_circuit_is_a_503(failing_client: AsyncClient) -> None:
    """503 says the service is refusing work it knows it cannot do, which is the truth."""

    response = await failing_client.get("/health/testing/unavailable")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/health/testing/timeout", "3"), ("/health/testing/unavailable", "10")],
)
async def test_both_tell_the_caller_when_to_come_back(
    failing_client: AsyncClient, path: str, expected: str
) -> None:
    """`Retry-After` in seconds, as an integer: RFC 9110 allows no fraction.

    The value is rounded up, never down. A client that returns early adds load to a dependency
    that has not recovered yet, which is the stampede the breaker exists to prevent.
    """

    response = await failing_client.get(path)

    assert response.headers["Retry-After"] == expected


async def test_a_sub_second_wait_still_rounds_up_to_one(failing_app: FastAPI) -> None:
    @failing_app.get("/health/testing/soon")
    async def _soon() -> None:
        raise DependencyUnavailableError("probing", retry_after=0.004)

    transport = ASGITransport(app=failing_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health/testing/soon")

    # `Retry-After: 0` invites an immediate retry, so the floor is one second.
    assert response.headers["Retry-After"] == "1"


async def test_the_envelope_keeps_its_shape(failing_client: AsyncClient) -> None:
    """Same four keys as every other error. A new status must not mean a new contract."""

    body = (await failing_client.get("/health/testing/timeout")).json()

    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id", "details"}
    assert body["error"]["request_id"]


async def test_the_details_never_carry_the_underlying_exception(
    failing_app: FastAPI,
) -> None:
    """A dependency failure names hosts, ports and credentials. None of that leaves the process."""

    @failing_app.get("/health/testing/leaky")
    async def _leaky() -> None:
        raise UpstreamTimeoutError(
            "mysql did not answer in time",
            retry_after=3.0,
            details={"dependency": "mysql"},
        )

    transport = ASGITransport(app=failing_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        body = (await ac.get("/health/testing/leaky")).json()

    assert body["error"]["details"] == {"dependency": "mysql"}
    rendered = str(body)
    for secret in ("aiomysql", "password", "3306", "Traceback"):
        assert secret not in rendered


async def test_the_request_id_header_survives_the_new_statuses(
    failing_client: AsyncClient,
) -> None:
    """The envelope and the header must quote the same id, or a report cannot be traced."""

    response = await failing_client.get("/health/testing/unavailable")

    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]


async def test_an_ordinary_domain_error_is_not_a_degradation(
    failing_app: FastAPI,
) -> None:
    """A 404 is an answer. Counting it here would bury the signal under normal traffic.

    Read off the default registry as a delta: the handler counts on the process-wide
    collectors, which this test shares with every other test in the run.
    """

    @failing_app.get("/health/testing/missing")
    async def _missing() -> None:
        raise ArticleNotFoundError("Article does not exist.")

    def total() -> float:
        return sum(
            sample.value
            for metric in REGISTRY.collect()
            if metric.name == "degraded_responses"
            for sample in metric.samples
            if sample.name == "degraded_responses_total"
        )

    before = total()
    transport = ASGITransport(app=failing_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health/testing/missing")

    assert response.status_code == 404
    assert total() == before


async def test_the_degraded_counter_names_the_route_and_the_reason(
    failing_client: AsyncClient,
) -> None:
    def count(route: str, reason: str) -> float:
        value = REGISTRY.get_sample_value(
            "degraded_responses_total", {"route": route, "reason": reason}
        )
        return 0.0 if value is None else value

    before = (
        count("/health/testing/timeout", "upstream_timeout"),
        count("/health/testing/unavailable", "breaker_open"),
    )

    await failing_client.get("/health/testing/timeout")
    await failing_client.get("/health/testing/unavailable")

    assert count("/health/testing/timeout", "upstream_timeout") == before[0] + 1
    assert count("/health/testing/unavailable", "breaker_open") == before[1] + 1
