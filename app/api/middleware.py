"""Authentication middleware.

Verifies the ``Authorization: Bearer`` JWT when present and attaches the caller's principal
(``{"id", "role"}``) to ``request.state`` for downstream dependencies.

``BaseHTTPMiddleware`` runs outside FastAPI's exception handlers, so failures here build the
canonical error envelope directly via :func:`error_response` rather than raising domain errors.
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.api.errors import error_response
from app.core.security import decode_access_token
from app.services.exceptions import UnauthenticatedError


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
        return await call_next(request)
