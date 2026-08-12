"""The request transaction must close before the response is sent.

``get_session`` commits on teardown, so if FastAPI tears the dependency down after the
response leaves the router, a write endpoint answers 201 for a row no other connection can
see yet — a read-your-writes violation, observed as register-then-login returning 401.

The invariant is asserted at the ASGI boundary rather than over a socket: the in-process
harness cannot see the symptom (``ASGITransport`` awaits the whole call, teardown
included), but it can see the cause, without a database.
``tests/integration/test_read_your_writes.py`` covers the symptom over real TCP.

The probe app here is deliberately bare. ``create_app()``'s four ``BaseHTTPMiddleware``
layers each pump the response through an anyio memory-object stream, so an observer bolted
to the outside of the chain races the teardown and can agree by luck. Adjacent to the
router the ordering is exact, and the dependency declaration is what this pins.
"""

from collections.abc import AsyncGenerator, MutableSequence
from typing import Any

from fastapi import FastAPI, HTTPException, params
from httpx import ASGITransport, AsyncClient
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.deps import SessionDep
from app.db.session import get_session

CLOSED = "session-closed"
SENT = "response-start"


class RecordResponseStart:
    """ASGI shim appending :data:`SENT` when the response headers leave the app."""

    def __init__(self, app: ASGIApp, events: MutableSequence[str]) -> None:
        self._app = app
        self._events = events

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def recording_send(message: Any) -> None:
            if message["type"] == "http.response.start":
                self._events.append(SENT)
            await send(message)

        await self._app(scope, receive, recording_send)


def _probe_app(events: MutableSequence[str]) -> FastAPI:
    """A bare app whose routes take the production ``SessionDep``.

    Test-local routes rather than a shipped endpoint: the scope is declared once on the
    shared alias in ``app/api/deps.py``, and that is what must hold, independently of which
    endpoints happen to use it today.
    """
    app = FastAPI()

    @app.get("/probe")
    async def probe(session: SessionDep) -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/boom")
    async def boom(session: SessionDep) -> dict[str, str]:
        raise HTTPException(status_code=418, detail="kaboom")

    async def tracking_session() -> AsyncGenerator[Any, None]:
        # A sentinel, not a session: neither probe route touches it. The override inherits
        # the scope declared on SessionDep, so this exercises the real declaration.
        try:
            yield object()
        finally:
            events.append(CLOSED)

    app.dependency_overrides[get_session] = tracking_session
    return app


async def _get(path: str, events: MutableSequence[str]) -> int:
    transport = ASGITransport(app=RecordResponseStart(_probe_app(events), events))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return (await ac.get(path)).status_code


async def test_session_closes_before_response_is_sent() -> None:
    """The happy path: teardown (the COMMIT) must precede the first response byte."""
    events: list[str] = []
    assert await _get("/probe", events) == 200
    assert events == [CLOSED, SENT]


async def test_session_closes_before_response_is_sent_on_error() -> None:
    """The failure path: teardown (the ROLLBACK) precedes the error envelope too."""
    events: list[str] = []
    assert await _get("/boom", events) == 418
    assert events == [CLOSED, SENT]


def test_session_dependency_is_function_scoped() -> None:
    """Guard the declaration itself, so the reason survives a refactor of the alias.

    FastAPI's default for a generator dependency is ``scope="request"``, which ends it
    after the response is sent; only ``"function"`` ends it before.
    """
    (marker,) = (m for m in SessionDep.__metadata__ if isinstance(m, params.Depends))  # type: ignore[attr-defined]
    assert marker.scope == "function"
