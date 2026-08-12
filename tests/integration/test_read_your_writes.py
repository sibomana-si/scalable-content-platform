"""Read-your-writes across requests, over a real socket.

Every other API test drives the app in-process through ``httpx.ASGITransport``, which awaits
the entire ASGI call — dependency teardown included — before handing back a response. That
makes the suite structurally blind to anything that happens between "the client has the
response" and "the request transaction ended". This test runs a real uvicorn server on an
ephemeral port so the client is a genuine HTTP peer, and asserts the guarantee a caller
actually relies on: a 201 means the row is there for the next request.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Iterator
from uuid import uuid4

import pytest
import uvicorn
from httpx import AsyncClient

from app.main import create_app

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple-42"
# One iteration reproduced the fault roughly 3 times in 5, so a handful of rounds turns a
# probabilistic symptom into a reliable signal.
ROUNDS = 10


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        # pytest owns the process signal handlers; uvicorn must not swap them out.
        yield


@contextlib.asynccontextmanager
async def _live_server() -> AsyncGenerator[str, None]:
    """Serve the real app on an ephemeral port for the duration of the block."""

    server = _Server(uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def test_register_is_visible_to_an_immediately_following_login(clean_db: None) -> None:
    """A 201 from register must be honoured by the very next login, with no grace period.

    ``Connection: close`` so each call is its own TCP connection, as two separate client
    processes would be — the shape in which this was first seen.
    """
    async with (
        _live_server() as base_url,
        AsyncClient(base_url=base_url, headers={"Connection": "close"}) as client,
    ):
        for attempt in range(ROUNDS):
            credentials = {"email": f"rww-{uuid4().hex}@example.com", "password": PASSWORD}

            registered = await client.post("/v1/auth/register", json=credentials)
            assert registered.status_code == 201

            logged_in = await client.post("/v1/auth/login", json=credentials)
            assert logged_in.status_code == 200, (
                f"round {attempt}: register returned 201 but the row was not yet visible "
                f"to login ({logged_in.status_code})"
            )
            assert logged_in.json()["access_token"]
