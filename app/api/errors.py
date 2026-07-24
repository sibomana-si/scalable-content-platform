"""
Canonical error envelope and the app-level handlers that
produce it: domain exceptions, request validation, and the 500 fallback.
"""

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.services.exceptions import (
    ArticleNotFoundError,
    ConflictError,
    DomainError,
    ForbiddenError,
    InvalidCursorError,
    PreconditionRequiredError,
    UnauthenticatedError,
)

_DOMAIN_STATUS: dict[type[DomainError], tuple[int, str]] = {
    UnauthenticatedError: (401, "UNAUTHENTICATED"),
    ForbiddenError: (403, "FORBIDDEN"),
    ArticleNotFoundError: (404, "ARTICLE_NOT_FOUND"),
    ConflictError: (409, "CONFLICT"),
    PreconditionRequiredError: (422, "VALIDATION_ERROR"),
    InvalidCursorError: (422, "VALIDATION_ERROR"),
}


def error_response(
    request: Request, status: int, code: str, message: str, details: dict | list | None = None
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
    )


def register_exception_handlers(app: FastAPI) -> None:
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status, code = _DOMAIN_STATUS[type(exc)]
        return error_response(request, status, code, exc.message, exc.details)

    for exc_type in _DOMAIN_STATUS:
        app.add_exception_handler(exc_type, domain_error)

    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
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
