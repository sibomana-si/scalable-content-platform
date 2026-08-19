"""The after-commit callback queue: pure, no database.

``get_session`` commits in its teardown, after the handler returns. Anything a handler runs
inline therefore runs before the row is durable. For cache invalidation that is a silent
correctness bug: a concurrent reader can miss, read the pre-commit row, and repopulate the
cache with the old value, which then survives its full TTL with no error and no metric.

The queue exists so invalidation waits for the commit. These tests pin the two rules that
make it safe — it drains only on success, and a failing callback never reaches the caller.
"""

import pytest

from app.db.after_commit import (
    after_commit,
    discard_after_commit,
    drain_after_commit,
    pending_after_commit,
)


class FakeSession:
    """Stands in for AsyncSession: only ``.info`` matters to the queue."""

    def __init__(self) -> None:
        self.info: dict = {}


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


async def test_a_queue_starts_empty(session):
    assert pending_after_commit(session) == []


async def test_a_registered_callback_does_not_run_yet(session):
    """Registering is not running. The whole point is to defer past the commit."""
    ran = []
    after_commit(session, lambda: ran.append("x"))

    assert ran == []
    assert len(pending_after_commit(session)) == 1


async def test_draining_runs_the_callbacks(session):
    ran = []

    async def callback():
        ran.append("x")

    after_commit(session, callback)
    await drain_after_commit(session)

    assert ran == ["x"]


async def test_callbacks_run_in_registration_order(session):
    ran = []

    for name in ("first", "second", "third"):
        after_commit(session, lambda n=name: ran.append(n))
    await drain_after_commit(session)

    assert ran == ["first", "second", "third"]


async def test_draining_empties_the_queue(session):
    """A second drain must not repeat the work; a double INCR would cost a cache generation."""
    ran = []
    after_commit(session, lambda: ran.append("x"))

    await drain_after_commit(session)
    await drain_after_commit(session)

    assert ran == ["x"]


async def test_a_synchronous_callback_is_accepted(session):
    ran = []
    after_commit(session, lambda: ran.append("x"))
    await drain_after_commit(session)

    assert ran == ["x"]


async def test_a_failing_callback_does_not_reach_the_caller(session):
    """The commit already succeeded. A cache error must not turn a 201 into a 500."""
    ran = []

    async def boom():
        raise RuntimeError("redis is down")

    after_commit(session, boom)
    after_commit(session, lambda: ran.append("after"))

    await drain_after_commit(session)  # must not raise

    assert ran == ["after"]  # and one failure does not cancel the rest


async def test_discarding_drops_the_queue_without_running_it(session):
    """A rolled-back write invalidates nothing: there is no new value to invalidate to."""
    ran = []
    after_commit(session, lambda: ran.append("x"))

    discard_after_commit(session)
    await drain_after_commit(session)

    assert ran == []


async def test_two_sessions_keep_separate_queues(session):
    """The queue lives on the session, so concurrent requests cannot drain each other's."""
    other = FakeSession()
    ran = []
    after_commit(session, lambda: ran.append("mine"))
    after_commit(other, lambda: ran.append("theirs"))

    await drain_after_commit(session)

    assert ran == ["mine"]
