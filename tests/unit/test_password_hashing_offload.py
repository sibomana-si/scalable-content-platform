"""Password hashing must not block the event loop.

Argon2id is deliberately expensive and memory-hard — ~145 ms and 64 MiB per hash at the
library defaults. Called inline from an ``async def`` handler it stalls the whole worker
process: every other in-flight request, including cheap cached reads, waits behind it, so a
handful of concurrent logins is enough to blow the 200 ms P95 SLO for unrelated traffic.

These tests pin the invariant (hashing runs off the loop thread, in a bounded pool) rather
than the timings it happens to produce, so they stay honest on a slow CI box.
"""

import asyncio
import threading
import time

import pytest

from app.config import get_settings
from app.core import security


@pytest.fixture(autouse=True)
def _fresh_pool() -> None:  # type: ignore[misc]
    """Give each test its own hashing pool, sized from that test's environment."""

    def reset() -> None:
        if security.get_password_executor.cache_info().currsize:
            security.get_password_executor().shutdown(wait=False)
        security.get_password_executor.cache_clear()
        get_settings.cache_clear()

    reset()
    yield
    reset()


class _RecordingHasher:
    """Stand-in for the Argon2 ``PasswordHasher``, recording the thread it runs on."""

    def __init__(self) -> None:
        self.threads: list[int] = []

    def hash(self, password: str) -> str:
        self.threads.append(threading.get_ident())
        return "$argon2id$stub"

    def verify(self, password_hash: str, password: str) -> bool:
        self.threads.append(threading.get_ident())
        return True


class _ConcurrencyProbe:
    """Counts how many hashes are in flight at once."""

    def __init__(self, hold: float = 0.05) -> None:
        self._hold = hold
        self._lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0

    def hash(self, password: str) -> str:
        with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        time.sleep(self._hold)
        with self._lock:
            self.in_flight -= 1
        return "$argon2id$stub"

    def verify(self, password_hash: str, password: str) -> bool:
        self.hash(password)
        return True


async def test_hash_password_runs_off_the_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hasher = _RecordingHasher()
    monkeypatch.setattr(security, "_hasher", hasher)

    digest = await security.hash_password("correct-horse-battery-staple-42")

    assert digest == "$argon2id$stub"
    assert hasher.threads, "the hasher was never called"
    assert threading.get_ident() not in hasher.threads


async def test_verify_password_runs_off_the_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hasher = _RecordingHasher()
    monkeypatch.setattr(security, "_hasher", hasher)

    assert await security.verify_password("pw", "$argon2id$stub") is True
    assert hasher.threads, "the hasher was never called"
    assert threading.get_ident() not in hasher.threads


async def test_concurrent_hashing_is_bounded_by_the_configured_pool_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unbounded offload trades an event-loop stall for a memory blow-up: each in-flight
    # Argon2id hash holds ~64 MiB, so the pool size is the real safety valve.
    monkeypatch.setenv("PASSWORD_HASH_MAX_THREADS", "3")
    get_settings.cache_clear()
    security.get_password_executor.cache_clear()
    probe = _ConcurrencyProbe()
    monkeypatch.setattr(security, "_hasher", probe)

    await asyncio.gather(*(security.hash_password("pw") for _ in range(9)))
    assert probe.peak <= 3


async def test_real_hashing_leaves_the_event_loop_responsive() -> None:
    """The end-to-end claim, with the real Argon2id cost — no stub."""
    ticks: list[float] = []

    async def heartbeat() -> None:
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.001)

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.01)  # let the heartbeat settle into its rhythm

    started = time.perf_counter()
    await security.hash_password("correct-horse-battery-staple-42")
    elapsed = time.perf_counter() - started

    # Let the heartbeat tick once more before cancelling. A tick is only recorded when the
    # loop gets control back, so a stall shows up as the gap bracketing it — cancel
    # immediately and the very gap under test is the one that never gets written down.
    await asyncio.sleep(0.01)
    beat.cancel()
    with pytest.raises(asyncio.CancelledError):
        await beat

    gaps = [later - earlier for earlier, later in zip(ticks, ticks[1:], strict=False)]
    assert elapsed > 0.02, "Argon2id finished too fast for this test to prove anything"
    assert len(gaps) >= 5, "the heartbeat never got to run"
    # Relative, not absolute: on a slow box the hash and the tolerated gap grow together.
    assert max(gaps) < elapsed / 3
