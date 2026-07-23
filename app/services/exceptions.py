"""Domain exceptions. Raised by services/dependencies; converted to the canonical error
envelope."""


class DomainError(Exception):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict = details or {}


class UnauthenticatedError(DomainError):
    """401 UNAUTHENTICATED: missing/invalid identity."""


class ForbiddenError(DomainError):
    """403 FORBIDDEN: authenticated but not owner/admin."""


class ArticleNotFoundError(DomainError):
    """404 ARTICLE_NOT_FOUND: nonexistent or soft-deleted article."""


class ConflictError(DomainError):
    """409 CONFLICT: stale optimistic-concurrency precondition or constraint clash."""


class PreconditionRequiredError(DomainError):
    """422 VALIDATION_ERROR: If-Match precondition missing or unparsable."""


class InvalidCursorError(DomainError):
    """422 VALIDATION_ERROR: pagination cursor failed strict decoding."""
