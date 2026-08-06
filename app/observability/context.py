"""Request-correlation helpers shared by the logging, metrics and tracing layers.

Kept free of Starlette middleware machinery so the rules — what a valid request id looks
like, which route label a request gets, who the caller is — are unit-testable without HTTP.
"""

import re
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from starlette.routing import Match

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
    downstream app has run — and it is absent entirely when a middleware short-circuits
    (a 401/403 from the auth policy never reaches the router). Rather than let every denied
    request collapse into ``__unmatched__`` — which would hide which endpoint is being
    probed — the routing table is consulted directly in that case.
    """
    path = getattr(scope.get("route"), "path", None)
    if isinstance(path, str):
        return path
    return _match_route_table(scope)


def _leaf_routes(routes: Any) -> Iterator[Any]:
    """Flatten the routing tree down to the routes that own a concrete path.

    ``include_router`` produces container nodes (FastAPI's ``_IncludedRouter``, Starlette's
    ``Mount``) that carry no path of their own; only their children do.
    """
    for route in routes:
        nested = getattr(route, "routes", None) or getattr(
            getattr(route, "original_router", None), "routes", None
        )
        if nested:
            yield from _leaf_routes(nested)
        else:
            yield route


def _match_route_table(scope: Any) -> str:
    """Resolve the templated path by asking the app's routes, as the router itself would."""
    routes = getattr(scope.get("app"), "routes", None)
    if not routes:
        return UNMATCHED_ROUTE

    partial = UNMATCHED_ROUTE
    for route in _leaf_routes(routes):
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        match, _ = route.matches(scope)
        if match is Match.FULL:
            return path
        if match is Match.PARTIAL and partial is UNMATCHED_ROUTE:
            partial = path  # right path, wrong method: a 405 still belongs to that endpoint
    return partial
