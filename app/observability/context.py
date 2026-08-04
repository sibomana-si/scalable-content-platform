"""Request-correlation helpers shared by the logging, metrics and tracing layers.

Kept free of Starlette middleware machinery so the rules — what a valid request id looks
like, which route label a request gets, who the caller is — are unit-testable without HTTP.
"""

import re
from typing import Any
from uuid import uuid4

# Inbound/outbound correlation header. A gateway or client may supply it; we echo it back.
REQUEST_ID_HEADER = "X-Request-ID"

# Label used when a request never matched a route (404s, malformed paths). Caller-controlled
# paths must never become label values — that is an unbounded-cardinality hole.
UNMATCHED_ROUTE = "__unmatched__"

# Deliberately narrow: id characters that are safe to embed in a log line and a header value.
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def new_request_id() -> str:
    """Mint a fresh correlation id (32-char hex)."""
    return uuid4().hex


def sanitize_request_id(value: str | None) -> str:
    """Return ``value`` if it is a safe correlation id, otherwise a freshly minted one.

    Inbound header values are attacker-controlled and get echoed into log records and error
    envelopes, so anything carrying a newline or control character is discarded rather than
    escaped — a forged id could otherwise fabricate log lines.
    """
    if value is not None and _REQUEST_ID_RE.fullmatch(value):
        return value
    return new_request_id()


def principal_user_id(state: Any) -> str | None:
    """The authenticated caller's id as a string, or ``None`` for anonymous/unauthenticated.

    Reads the principal that ``AuthMiddleware`` attaches to ``request.state``; tolerates the
    key being absent, since the outer logging layer also runs for requests that never reach
    (or are rejected before) authentication.
    """

    principal = getattr(state, "principal", None)
    if not isinstance(principal, dict):
        return None
    user_id = principal.get("id")
    return None if user_id is None else str(user_id)


def route_template(scope: Any) -> str:
    """The matched route's templated path (``/v1/articles/{article_id}``), or the unmatched
    constant.

    ``scope["route"]`` is set by the router during dispatch, so this must be read after the
    downstream app has run.
    """
    path = getattr(scope.get("route"), "path", None)
    return path if isinstance(path, str) else UNMATCHED_ROUTE
