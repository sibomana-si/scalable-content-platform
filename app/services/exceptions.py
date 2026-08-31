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


class DependencyDownError(DomainError):
    """A dependency did not answer. Carries how long the caller should wait before retrying.

    `retry_after` is a number of seconds, not a date. It becomes the `Retry-After` header, so a
    client that honors it comes back after the circuit has had a chance to close rather than
    adding load to a dependency that is already failing.
    """

    def __init__(self, message: str, *, retry_after: float, details: dict | None = None) -> None:
        super().__init__(message, details)
        self.retry_after = retry_after


class UpstreamTimeoutError(DependencyDownError):
    """504 UPSTREAM_TIMEOUT: a dependency did not answer inside its timeout."""


class DependencyUnavailableError(DependencyDownError):
    """503 SERVICE_UNAVAILABLE: a dependency refused the call, or the circuit is open."""
