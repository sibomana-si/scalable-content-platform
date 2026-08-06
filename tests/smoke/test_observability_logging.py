"""Wiring checks for the access log: one correlated JSON line per request, no containers.

Verification that the middleware chain is assembled in the documented order and that
the log line, the response header and the error envelope all carry the same request id.
"""

import io
import json
from collections.abc import AsyncGenerator, Iterator

import pytest
import pytest_asyncio
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from app.main import create_app
from app.observability.context import REQUEST_ID_HEADER, UNMATCHED_ROUTE
from app.observability.logging import configure_logging

ACCESS_LOG_EVENT = "http.request"
DOCUMENTED_FIELDS = {
    "timestamp",
    "level",
    "message",
    "request_id",
    "user_id",
    "route",
    "latency_ms",
    "status",
}


@pytest.fixture
def logs() -> Iterator[io.StringIO]:
    """Render into a buffer, in JSON, regardless of the developer's local LOG_FORMAT."""
    buffer = io.StringIO()
    configure_logging(level="INFO", fmt="json", stream=buffer)
    yield buffer
    structlog.reset_defaults()
    configure_logging()


@pytest.fixture
def app() -> FastAPI:
    app = create_app()

    # Public (the /health prefix needs no token) so these probe the log, not the auth policy.
    @app.get("/health/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom: this string must never reach the client")

    @app.get("/health/echo/{item_id}")
    async def echo(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    # raise_app_exceptions=False: let the 500 be observed as a response, as a real server would.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def records(logs: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in logs.getvalue().splitlines() if line.strip()]


def access_logs(logs: io.StringIO) -> list[dict]:
    return [r for r in records(logs) if r.get("message") == ACCESS_LOG_EVENT]


async def test_one_request_emits_exactly_one_access_log_with_every_documented_field(
    client: AsyncClient, logs: io.StringIO
) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200

    entries = access_logs(logs)

    assert len(entries) == 1
    assert set(entries[0]) >= DOCUMENTED_FIELDS
    assert entries[0]["level"] == "info"
    assert entries[0]["status"] == 200
    assert entries[0]["route"] == "/health/live"
    assert entries[0]["method"] == "GET"
    assert entries[0]["user_id"] is None  # anonymous caller
    assert isinstance(entries[0]["latency_ms"], (int, float))
    assert entries[0]["latency_ms"] >= 0


async def test_route_is_the_templated_path_not_the_concrete_url(
    client: AsyncClient, logs: io.StringIO
) -> None:
    await client.get("/health/echo/42")
    assert access_logs(logs)[0]["route"] == "/health/echo/{item_id}"


async def test_unmatched_paths_collapse_to_a_constant(
    client: AsyncClient, logs: io.StringIO
) -> None:
    response = await client.get("/health/does-not-exist")
    assert response.status_code == 404

    entry = access_logs(logs)[0]
    assert entry["route"] == UNMATCHED_ROUTE
    assert entry["status"] == 404


async def test_authenticated_requests_log_the_caller_id(
    client: AsyncClient, logs: io.StringIO
) -> None:
    token = create_access_token(sub="7", role="user")
    await client.get("/health/live", headers={"Authorization": f"Bearer {token}"})
    assert access_logs(logs)[0]["user_id"] == "7"


async def test_inbound_request_id_is_echoed_in_the_header_and_the_log(
    client: AsyncClient, logs: io.StringIO
) -> None:
    response = await client.get("/health/live", headers={REQUEST_ID_HEADER: "trace-me-1"})
    assert response.headers[REQUEST_ID_HEADER] == "trace-me-1"
    assert access_logs(logs)[0]["request_id"] == "trace-me-1"


async def test_a_generated_request_id_is_returned_when_none_is_supplied(
    client: AsyncClient, logs: io.StringIO
) -> None:
    response = await client.get("/health/live")
    request_id = response.headers[REQUEST_ID_HEADER]
    assert len(request_id) == 32
    assert access_logs(logs)[0]["request_id"] == request_id


async def test_a_forged_request_id_is_replaced_not_propagated(
    client: AsyncClient, logs: io.StringIO
) -> None:
    forged = 'evil\n{"message": "http.request", "status": 200}'
    response = await client.get("/health/live", headers={REQUEST_ID_HEADER: forged})

    assert response.headers[REQUEST_ID_HEADER] != forged
    entries = access_logs(logs)
    assert len(entries) == 1  # the injected line did not become a second record
    assert "evil" not in entries[0]["request_id"]


async def test_error_envelope_carries_the_same_request_id_as_the_log(
    client: AsyncClient, logs: io.StringIO
) -> None:
    # A 401 is produced inside AuthMiddleware, which sits below the request-id layer:
    # this is what proves the middleware ordering feeds `error_response`'s request_id seam.
    response = await client.post(
        "/v1/articles",
        json={"title": "t", "body": "b"},
        headers={REQUEST_ID_HEADER: "envelope-1"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["request_id"] == "envelope-1"

    entry = access_logs(logs)[0]
    assert entry["request_id"] == "envelope-1"
    # The router never ran, so scope["route"] is unset; the label still has to name the
    # endpoint that was probed, otherwise every denial disappears into `__unmatched__`.
    assert entry["route"] == "/v1/articles"


async def test_failed_authentication_is_audited(client: AsyncClient, logs: io.StringIO) -> None:
    response = await client.get("/health/live", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401

    audit = [r for r in records(logs) if r.get("message") == "auth.failed"]

    assert len(audit) == 1
    assert audit[0]["level"] == "warning"
    assert audit[0]["request_id"]
    assert "not-a-jwt" not in json.dumps(audit[0])  # the rejected token is never logged


async def test_unhandled_errors_log_at_error_level_without_leaking_to_the_client(
    client: AsyncClient, logs: io.StringIO
) -> None:
    response = await client.get("/health/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "kaboom" not in response.text

    entry = access_logs(logs)[0]
    assert entry["level"] == "error"
    assert entry["status"] == 500
    assert "kaboom" in json.dumps(entry)  # the operator, unlike the client, gets the detail
