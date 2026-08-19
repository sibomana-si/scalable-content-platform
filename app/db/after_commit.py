"""A per-session queue of work that must wait for the transaction to commit.

``get_session`` commits in its teardown, after the handler returns. Anything a service runs
inline therefore runs before the row is durable. For cache invalidation that is a silent
correctness bug: a concurrent reader can miss, read the pre-commit row, and repopulate the
cache with the old value — which then survives its full TTL, with no error and no metric to
show for it.

So a write registers its invalidation here, and ``get_session`` drains the queue once the
transaction block exits cleanly. A rolled-back write drains nothing: there is no new value to
invalidate to.

The queue lives on ``session.info``, which SQLAlchemy provides for exactly this and which is
per-session, so two concurrent requests cannot drain each other's work.
"""

import inspect
from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_QUEUE_KEY = "after_commit_callbacks"

Callback = Callable[[], Any]


def after_commit(session: Any, callback: Callback) -> None:
    """Register work to run once the request transaction commits."""
    session.info.setdefault(_QUEUE_KEY, []).append(callback)


def pending_after_commit(session: Any) -> list[Callback]:
    """The callbacks registered so far. For tests and for debugging."""
    return list(session.info.get(_QUEUE_KEY, []))


def discard_after_commit(session: Any) -> None:
    """Drop the queue without running it. Used when the transaction rolls back."""
    session.info.pop(_QUEUE_KEY, None)


async def drain_after_commit(session: Any) -> None:
    """Run every registered callback, in order, then empty the queue.

    A callback that raises is logged and skipped. The commit already succeeded, so the client
    is owed its 201 — turning a cache failure into a 500 would trade a slow read for a lost
    write. One failure does not cancel the callbacks behind it.
    """
    callbacks = session.info.pop(_QUEUE_KEY, [])
    for callback in callbacks:
        try:
            result = callback()
            if inspect.isawaitable(result):
                await result
        except Exception as error:  # noqa: BLE001
            log.warning("after_commit.failed", error=str(error))
