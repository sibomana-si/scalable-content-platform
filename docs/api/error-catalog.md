# Error Catalog

> **Status:** 🟥 Draft

Consistent error responses are an explicit requirement. Define one canonical schema and enumerate codes.

## Canonical Error Schema
```json
{
  "error": {
    "code": "ARTICLE_NOT_FOUND",
    "message": "Human-readable description.",
    "request_id": "uuid-for-tracing",
    "details": {}
  }
}
```

## Codes
| Code | HTTP | Meaning | Notes |
|---|---|---|---|
| `VALIDATION_ERROR` | 422 | Request failed validation | Include field details |
| `UNAUTHENTICATED` | 401 | Missing/invalid token | |
| `FORBIDDEN` | 403 | Authenticated but lacks role | |
| `ARTICLE_NOT_FOUND` | 404 | Article does not exist | |
| `CONFLICT` | 409 | Duplicate/constraint violation, or **stale write** (optimistic-concurrency precondition failed) | Lost-update protection on PUT/DELETE — see [PRD §13 D11](../requirements/product-requirements.md) |
| `RATE_LIMITED` | 429 | Too many requests | **Reserved / post-MVP** — rate limiting deferred ([PRD §13 D2](../requirements/product-requirements.md)) |
| `UPSTREAM_TIMEOUT` | 504 | DB/cache timeout | Ties to resilience |
| `INTERNAL_ERROR` | 500 | Unexpected failure | Never leak internals |

_Always include `request_id` to correlate with logs and traces ([observability](../observability/observability-guide.md))._
