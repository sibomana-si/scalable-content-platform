# Error Catalog

> **Status:** 🟩 Current — matches the canonical envelope emitted by `app/api/errors.py`.

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
|---|---|---|-|
| `VALIDATION_ERROR` | 422 | Request failed validation | Include field details |
| `UNAUTHENTICATED` | 401 | Missing/invalid caller identity | Missing, malformed, expired, or tampered `Authorization: Bearer` JWT on a protected route, invalid login credentials, or a token whose subject no longer resolves to a `users` row. Always a generic message (no reason leaked, no user enumeration). |
| `FORBIDDEN` | 403 | Authenticated but lacks role | |
| `ARTICLE_NOT_FOUND` | 404 | Article does not exist | |
| `CONFLICT` | 409 | Duplicate/constraint violation, or **stale write** (optimistic-concurrency precondition failed) | Lost-update protection on PUT/DELETE — see [PRD §13 D11](../requirements/product-requirements.md) |
| `RATE_LIMITED` | 429 | Too many requests | **Reserved / post-MVP** — rate limiting deferred ([PRD §13 D2](../requirements/product-requirements.md)) |
| `UPSTREAM_TIMEOUT` | 504 | A dependency did not answer inside its timeout | Carries `Retry-After` in whole seconds. The dependency is slow, not gone — see [fault-tolerance-design](../resilience/fault-tolerance-design.md) |
| `SERVICE_UNAVAILABLE` | 503 | A dependency refused the call, or the instance is at capacity | Carries `Retry-After`. Two causes, one code: an open circuit breaker, and load shedding at `MAX_INFLIGHT_REQUESTS`. Both mean "retry later", which is all a client can act on |
| `INTERNAL_ERROR` | 500 | Unexpected failure | Never leak internals |

_Always include `request_id` to correlate with logs and traces ([observability](../observability/observability-guide.md))._

## The 5xx split

`500` and the two resilience codes say opposite things about the server, so they are never
interchangeable.

- `500 INTERNAL_ERROR` — the server did something it did not intend. Nobody can predict when it
  clears, so there is no `Retry-After`.
- `503 SERVICE_UNAVAILABLE` and `504 UPSTREAM_TIMEOUT` — the server worked exactly as designed
  and reached a ceiling it was given. It knows roughly when to try again, so it says so.

The difference is what a client does next. A client that retries a 500 repeats a fault; a client
that honors the `Retry-After` on a 503 comes back after the circuit has had a chance to close.
`degraded_responses_total{route,reason}` counts the second kind, so the Resilience dashboard can
tell a deliberate refusal from a crash.
