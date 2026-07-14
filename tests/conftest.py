"""Shared test fixtures.

The API harness is an in-process httpx ``AsyncClient`` over ``ASGITransport`` (no live server).
Integration fixtures gate on **reachability** (is the TCP port open?), not on a successful query:
a local ``pytest`` with no compose stack skips integration, while CI - which provisions both
services - runs them and fails loudly if a reachable dependency is misconfigured
(rather than masking it as a skip).
"""

import socket
from collections.abc import AsyncGenerator
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import create_app


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def db_available() -> None:
    """Skip unless the MySQL port is reachable (present-but-broken → let the test fail)."""

    settings = get_settings()
    if not _port_open(settings.mysql_host, settings.mysql_port):
        pytest.skip(f"MySQL not reachable at {settings.mysql_host}:{settings.mysql_port}")


@pytest.fixture
def redis_available() -> None:
    """Skip unless the Redis port is reachable (present-but-broken → let the test fail)."""

    url = urlparse(get_settings().redis_url)
    host, port = url.hostname or "localhost", url.port or 6379
    if not _port_open(host, port):
        pytest.skip(f"Redis not reachable at {host}:{port}")
