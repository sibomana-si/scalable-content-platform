"""The middleware chain: request id → access log → authentication → RBAC.

Registration order in :func:`app.main.create_app` is reversed by Starlette, so the chain
runs outside in as:

``RequestIDMiddleware`` → ``AccessLogMiddleware`` → ``MetricsMiddleware`` →
``LoadShedMiddleware`` → ``AuthMiddleware``

The correlation id is minted at the outermost layer so every inner layer — including the
``error_response`` envelopes built inside ``AuthMiddleware`` — can quote it. All layers share
one ``request.state``, which is backed by the ASGI ``scope["state"]`` dict.

Access control is the single enforcement point for authorization: it verifies the
``Authorization: Bearer`` JWT when present, attaches the caller's principal
(``{"id", "role"}``) to ``request.state``, then applies the route-based role policy.
Per-resource ownership (author-or-admin on ``{id}``) stays in the service layer.

The policy is split into two functions, :func:`route_requirement` (what a route needs) and
:func:`authorize` (does this principal satisfy it), so the rules can be tested without HTTP.
``authorize`` checks authentication (401) before role (403).

``BaseHTTPMiddleware`` runs outside FastAPI's exception handlers, so failures here build the
canonical error envelope directly via :func:`error_response` rather than raising domain errors.
"""

from enum import Enum
from time import perf_counter
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.api.errors import error_response, retry_after_header
from app.core.security import decode_access_token
from app.observability.context import (
    REQUEST_ID_HEADER,
    principal_user_id,
    route_template,
    sanitize_request_id,
)
from app.observability.logging import get_logger
from app.observability.metrics import (
    METRICS,
    METRICS_PATH,
    PROBE_PATHS,
    Metrics,
    observe_degraded_response,
    observe_request,
    route_label,
    track_inflight,
)
from app.observability.tracing import annotate_current_span
from app.services.exceptions import UnauthenticatedError

Principal = dict[str, str]

logger = get_logger(__name__)


class Requirement(Enum):
    """Access level a route demands."""

    PUBLIC = "public"  # no token needed
    AUTHENTICATED = "authenticated"  # any valid token
    ADMIN = "admin"  # token with the admin role


# Prefixes that are reachable without a token. Auth endpoints and the API docs are public;
# health probes must answer before/without auth; Prometheus scrapes /metrics with no
# credentials and is kept off the public gateway instead.
_PUBLIC_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json", "/v1/auth", METRICS_PATH)
_ADMIN_PREFIX = "/v1/admin"
_ARTICLES_PREFIX = "/v1/articles"


def route_requirement(method: str, path: str) -> Requirement:
    """Classify a request into the access level it requires.

    Unknown routes default to AUTHENTICATED so a new endpoint is never accidentally public.
    """

    if path == "/" or any(path == p or path.startswith(p + "/") for p in _PUBLIC_PREFIXES):
        return Requirement.PUBLIC
    if path == _ADMIN_PREFIX or path.startswith(_ADMIN_PREFIX + "/"):
        return Requirement.ADMIN
    if path == _ARTICLES_PREFIX or path.startswith(_ARTICLES_PREFIX):
        # Reads are public, writes need a token (ownership is checked in the service).
        return Requirement.PUBLIC if method.upper() == "GET" else Requirement.AUTHENTICATED
    return Requirement.AUTHENTICATED


def authorize(requirement: Requirement, principal: Principal | None) -> tuple[int, str, str] | None:
    """Return ``None`` if the principal may proceed, else a ``(status, code, message)`` denial.

    Authentication is evaluated before role, so a missing principal yields 401 even on an
    ADMIN route (401 wins before 403).
    """

    if requirement is Requirement.PUBLIC:
        return None
    if principal is None:
        return (401, "UNAUTHENTICATED", "Authentication is required.")
    if requirement is Requirement.ADMIN and principal.get("role") != "admin":
        return (403, "FORBIDDEN", "Administrator privileges are required.")
    return None


def extract_bearer_token(request: Request) -> str | None:
    """Return the token from an ``Authorization: Bearer <token>`` header, or None."""

    header = request.headers.get("Authorization")
    if header is None:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Outermost layer: establish the correlation id for logs, traces and error envelopes.

    An inbound ``X-Request-ID`` (from a gateway or a client) is honoured when it is
    well-formed and replaced otherwise — see :func:`sanitize_request_id`. The id is bound
    into the structlog context, so every record emitted downstream carries it without the
    call site having to pass it around.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = sanitize_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id

        # Clear first: the contextvars are inherited from whatever task spawned this one,
        # so a stale binding from an earlier request must never survive into this record.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Closes the logs<->traces loop in both directions. `None` whenever tracing is off,
        # which is the default; the key is then simply absent from the log line.
        trace_id = annotate_current_span(request_id)
        if trace_id is not None:
            structlog.contextvars.bind_contextvars(trace_id=trace_id)

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit exactly one structured access-log record per request.

    The route label is read after the downstream app has run, because the router only sets
    ``scope["route"]`` once it has matched — that is what makes the field the templated path
    (``/v1/articles/{article_id}``) instead of a high-cardinality concrete URL.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Log-and-re-raise: the 500 envelope is still built by the app's exception
            # handler, but the traceback would otherwise be lost to the operator.
            logger.exception("http.request", **self._fields(request, 500, started))
            raise

        fields = self._fields(request, response.status_code, started)
        if response.status_code >= 500:
            logger.error("http.request", **fields)
        elif response.status_code >= 400:
            logger.warning("http.request", **fields)
        else:
            logger.info("http.request", **fields)
        return response

    @staticmethod
    def _fields(request: Request, status: int, started: float) -> dict[str, Any]:
        # `request_id` is merged in from the structlog context bound by RequestIDMiddleware.
        return {
            "user_id": principal_user_id(request.state),
            "route": route_template(request.scope),
            "method": request.method,
            "status": status,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
        }


class MetricsMiddleware(BaseHTTPMiddleware):
    """RED instrumentation: one counter increment and one latency observation per request.

    Sits below the access log and above authentication, so requests rejected by the auth
    policy still count towards the error rate — a spike of 401s is exactly the kind of thing
    the dashboard has to show.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # An unhandled error becomes a 500 further out; count it as one before re-raising,
            # or the error-rate SLI would silently under-report the worst failures.
            self._observe(request, 500, started)
            raise

        self._observe(request, response.status_code, started)
        return response

    @staticmethod
    def _observe(request: Request, status: int, started: float) -> None:
        route = route_label(request.scope)
        if route == METRICS_PATH or route in PROBE_PATHS:
            # Neither scraping nor probing is traffic: self-instrumentation would make the
            # scrape interval look like a request rate, and probes would put a floor under
            # every ratio computed from these counters.
            return
        observe_request(route, request.method, status, perf_counter() - started)


class LoadShedMiddleware(BaseHTTPMiddleware):
    """Refuse work past a measured ceiling, instead of queueing it.

    M6 finding F5: past the knee, requests were lost at the connection level. The client
    counted a dropped iteration, the server recorded no error, and no signal anywhere named
    the cause. An instance that cannot serve a request must refuse it with a status the caller
    can act on, and it must do so while the refusal is still cheap.

    The counter is a plain integer, not a semaphore. A semaphore makes the caller wait for a
    slot, and a waiting request is the thing this layer exists to prevent: it holds memory, it
    holds a connection, and it disappoints the caller later instead of now. Waiting on nothing
    also means no lock is needed — the event loop runs one task at a time between awaits, and
    the counter never changes across one.

    Probes and the metrics scrape are exempt. A shed liveness probe is a restart, a shed
    readiness probe removes an instance that is still serving, and a shed scrape is a gap in
    the data over exactly the window that explains the incident.
    """

    def __init__(
        self,
        app: Any,
        *,
        max_inflight: int,
        retry_after_seconds: float,
        metrics: Metrics = METRICS,
    ) -> None:
        if max_inflight < 1:
            raise ValueError(
                "MAX_INFLIGHT_REQUESTS must be at least 1; a lower ceiling sheds every "
                "request, which is an outage spelled as a configuration."
            )
        if retry_after_seconds <= 0:
            raise ValueError(
                "SHED_RETRY_AFTER_SECONDS must be positive; a non-positive wait tells every "
                "shed caller to come back at once, which is the stampede shedding prevents."
            )
        super().__init__(app)
        self._max_inflight = max_inflight
        self._retry_after = retry_after_seconds
        self._metrics = metrics
        self._inflight = 0

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path == METRICS_PATH or path in PROBE_PATHS:
            return await call_next(request)

        if self._inflight >= self._max_inflight:
            # The router never ran, so `route_label` resolves the template from the routing
            # table instead of `scope["route"]`. That keeps a shed request attributable to the
            # endpoint it asked for, which is what an operator needs to know what is saturating.
            observe_degraded_response(
                route_label(request.scope), "load_shed", metrics=self._metrics
            )
            logger.warning("request.shed", path=path, inflight=self._inflight)
            return error_response(
                request,
                503,
                "SERVICE_UNAVAILABLE",
                "The service is at capacity. Retry shortly.",
                headers=retry_after_header(self._retry_after),
            )

        self._inflight += 1
        track_inflight(1, metrics=self._metrics)
        try:
            return await call_next(request)
        finally:
            # A leaked slot is worse than no ceiling: the instance sheds everything, for good.
            self._inflight -= 1
            track_inflight(-1, metrics=self._metrics)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.principal = None
        token = extract_bearer_token(request)
        if token is not None:
            try:
                claims = decode_access_token(token)
            except UnauthenticatedError:
                # A supplied-but-invalid token is always a hard 401, on any route.
                logger.warning("auth.failed", reason="invalid_token", path=request.url.path)
                return error_response(request, 401, "UNAUTHENTICATED", "Invalid or expired token.")
            request.state.principal = {"id": claims["sub"], "role": claims["role"]}

        requirement = route_requirement(request.method, request.url.path)
        denial = authorize(requirement, request.state.principal)
        if denial is not None:
            status, code, _ = denial
            logger.warning(
                "auth.denied",
                status=status,
                code=code,
                requirement=requirement.value,
                user_id=principal_user_id(request.state),
                path=request.url.path,
            )
            return error_response(request, *denial)
        return await call_next(request)
