"""Authentication + RBAC middleware.

The single enforcement point for access control: it verifies the ``Authorization: Bearer``
JWT when present, attaches the caller's principal (``{"id", "role"}``) to ``request.state``,
then applies the route-based role policy. Per-resource ownership (author-or-admin on ``{id}``)
stays in the service layer.

The policy is split into two functions, :func:`route_requirement` (what a route needs) and
:func:`authorize` (does this principal satisfy it), so the rules can be tested without HTTP.
``authorize`` checks authentication (401) before role (403).

``BaseHTTPMiddleware`` runs outside FastAPI's exception handlers, so failures here build the
canonical error envelope directly via :func:`error_response` rather than raising domain errors.
"""

from enum import Enum

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.api.errors import error_response
from app.core.security import decode_access_token
from app.services.exceptions import UnauthenticatedError

Principal = dict[str, str]


class Requirement(Enum):
    """Access level a route demands."""

    PUBLIC = "public"  # no token needed
    AUTHENTICATED = "authenticated"  # any valid token
    ADMIN = "admin"  # token with the admin role


# Prefixes that are reachable without a token. Auth endpoints and the API docs are public;
# health probes must answer before/without auth.
_PUBLIC_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json", "/v1/auth")
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


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.principal = None
        token = extract_bearer_token(request)
        if token is not None:
            try:
                claims = decode_access_token(token)
            except UnauthenticatedError:
                # A supplied-but-invalid token is always a hard 401, on any route.
                return error_response(request, 401, "UNAUTHENTICATED", "Invalid or expired token.")
            request.state.principal = {"id": claims["sub"], "role": claims["role"]}

        requirement = route_requirement(request.method, request.url.path)
        denial = authorize(requirement, request.state.principal)
        if denial is not None:
            return error_response(request, *denial)
        return await call_next(request)
