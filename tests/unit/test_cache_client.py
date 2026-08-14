"""The shared Redis client must be bounded.

``redis.asyncio`` defaults to ``socket_timeout=None`` — a reachable-but-unresponsive server
(blackholed by a security group, or mid-failover) then hangs every command forever. That is
not a cache outage, it is a caller outage: whatever resources the caller holds while waiting
are held for good. The cache-aside read path (M5) inherits this client, so the bound belongs
here rather than at each call site.
"""

import pytest

from app.cache.client import get_redis
from app.config import get_settings


@pytest.fixture(autouse=True)
def _fresh_client() -> None:  # type: ignore[misc]
    def reset() -> None:
        get_redis.cache_clear()
        get_settings.cache_clear()

    reset()
    yield
    reset()


def test_client_applies_the_configured_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "1.5")
    monkeypatch.setenv("REDIS_SOCKET_CONNECT_TIMEOUT", "0.75")

    kwargs = get_redis().connection_pool.connection_kwargs

    assert kwargs["socket_timeout"] == 1.5
    assert kwargs["socket_connect_timeout"] == 0.75


def test_client_is_bounded_by_default() -> None:
    # The default matters more than the knob: an unset timeout is the failure mode.
    kwargs = get_redis().connection_pool.connection_kwargs

    assert kwargs["socket_timeout"] is not None
    assert kwargs["socket_connect_timeout"] is not None
