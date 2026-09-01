"""
Canonical error envelope and the app-level handlers that
produce it: domain exceptions, request validation, and the 500 fallback.
"""

from math import ceil
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.observability.metrics import observe_degraded_response, route_label
from app.services.exceptions import (
    ArticleNotFoundError,
    ConflictError,
    DependencyDownError,
    DependencyUnavailableError,
    DomainError,
    ForbiddenError,
    InvalidCursorError,
    PreconditionRequiredError,
    UnauthenticatedError,
    UpstreamTimeoutError,
)

_DOMAIN_STATUS: dict[type[DomainError], tuple[int, str]] = {
    UnauthenticatedError: (401, "UNAUTHENTICATED"),
    ForbiddenError: (403, "FORBIDDEN"),
    ArticleNotFoundError: (404, "ARTICLE_NOT_FOUND"),
    ConflictError: (409, "CONFLICT"),
    PreconditionRequiredError: (422, "VALIDATION_ERROR"),
    InvalidCursorError: (422, "VALIDATION_ERROR"),
    # A bounded dependency call that reached its ceiling. Both are 5xx, and neither is a 500:
    # the server worked exactly as designed, so "try again later" is the honest answer.
    UpstreamTimeoutError: (504, "UPSTREAM_TIMEOUT"),
    DependencyUnavailableError: (503, "SERVICE_UNAVAILABLE"),
}

# Why the response was less than the full behavior. A slow dependency and a refused one need
# different fixes, so they are different series rather than one "degraded" count.
_DEGRADED_REASONS: dict[type[DomainError], str] = {
    UpstreamTimeoutError: "upstream_timeout",
    DependencyUnavailableError: "breaker_open",
}


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    details: dict | list | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or uuid4().hex
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "details": details or {},
            }
        },
        headers=headers,
    )


def retry_after_header(seconds: float) -> dict[str, str]:
    """``Retry-After`` in whole seconds, rounded up and never below one.

    RFC 9110 allows no fraction, and rounding down is the wrong direction: a client that comes
    back early adds load to a dependency that has not recovered, which is the stampede the
    breaker exists to prevent. ``Retry-After: 0`` is an invitation to retry at once, so one
    second is the floor.
    """

    return {"Retry-After": str(max(1, ceil(seconds)))}


def register_exception_handlers(app: FastAPI) -> None:
    async def domain_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, DomainError)
        status, code = _DOMAIN_STATUS[type(exc)]
        headers = None
        if isinstance(exc, DependencyDownError):
            headers = retry_after_header(exc.retry_after)
            # Counted here rather than at the raise site: this is the one place that knows the
            # answer actually reached the client, and the route the caller asked for.
            observe_degraded_response(route_label(request.scope), _DEGRADED_REASONS[type(exc)])
        return error_response(request, status, code, exc.message, exc.details, headers)

    for exc_type in _DOMAIN_STATUS:
        app.add_exception_handler(exc_type, domain_error)

    async def validation_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        return error_response(
            request,
            422,
            "VALIDATION_ERROR",
            "Request failed validation.",
            jsonable_encoder(exc.errors()),
        )

    app.add_exception_handler(RequestValidationError, validation_error)

    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals: generic message, empty details
        return error_response(request, 500, "INTERNAL_ERROR", "An unexpected error occurred.")

    app.add_exception_handler(Exception, internal_error)
