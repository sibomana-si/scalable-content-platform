# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- Walking skeleton: `create_app()` factory, lazy async DB/Redis clients, and `/health/live` +
  `/health/ready` Kubernetes probe endpoints.
- Article CRUD endpoints (FR-004) under `/v1/articles` with the canonical error envelope
  (including `request_id`), Pydantic input bounds, and soft-delete.
- Keyset pagination and `author` filter for the article list (FR-005): opaque base64url cursor,
  `limit` 1–100 (default 20), newest-first with `id` tiebreaker, soft-deleted rows excluded.
- Optimistic concurrency on writes: `If-Match` precondition with single-statement compare-and-set
  (missing → 422, stale → 409), enforced under a transaction-per-request boundary.
- JWT authentication (FR-001/FR-002): `POST /v1/auth/register` (Argon2id password hashing, default
  `user` role, 201 with no password echoed, duplicate email → 409) and `POST /v1/auth/login` (HS256
  access token with `sub`/`role`/`iat`/`exp`, 15-min TTL, generic timing-equalized 401, no user
  enumeration). Token verification runs in an `AuthMiddleware` that attaches the caller's principal;
  `get_current_user` resolves it to the live `users` row. Replaces the temporary `X-User-Id` dev stub.
- JWT signing configured from the environment: `JWT_SECRET` (held as `SecretStr`, kept out of
  repr/logs), `JWT_ALGORITHM` (default HS256), `JWT_EXPIRE_SECONDS`; the app fails closed if the
  signing key is unset rather than signing with an empty key.
- RBAC enforcement (FR-003) in `AuthMiddleware` via a pure, unit-tested route policy
  (`route_requirement` → PUBLIC/AUTHENTICATED/ADMIN, `authorize`); 401 (authentication) is enforced
  before 403 (role), unknown routes default to authenticated, and per-resource ownership
  (author-or-admin) stays in the service layer with no 404 existence leak.
- Registration input validation (FR-001): `RegisterIn` now enforces `EmailStr` format + case
  normalization (≤ 254 chars) and the password policy — min 12 / max 128 chars plus breached-password
  screening via a pluggable `BreachedPasswordChecker` (offline bundled blocklist by default; live
  HIBP k-anonymity is a documented seam). Bad email / short / breached / oversized / missing fields →
  `422` with field-level details. Adds the `email-validator` dependency.
- Structured JSON logging (structlog): one access-log line per request on stdout carrying the
  documented fields (`timestamp`, `level`, `message`, `request_id`, `user_id`, `route`,
  `latency_ms`, `status`, `method`), with `route` as the templated path and unmatched requests
  collapsed to a cardinality-safe constant. A `redact_secrets` processor censors secret-bearing
  keys at any depth, and failed/denied authentication is audited (`auth.failed`/`auth.denied`)
  without ever logging the rejected token. `LOG_FORMAT=console` selects a human-readable
  renderer for local development.
- Request correlation: `RequestIDMiddleware` accepts a well-formed inbound `X-Request-ID`
  (rejecting anything that could forge a log line), otherwise mints one; the id is bound into
  the log context, echoed in the `X-Request-ID` response header, and is the value the canonical
  error envelope's `request_id` now reports — including for 401s rejected in middleware.
- Prometheus RED metrics and an unauthenticated `GET /metrics` scrape endpoint:
  `http_requests_total{route,method,status}`, `http_request_duration_seconds{route,method}`
  (buckets placed on the 200 ms / 450 ms SLO edges so `histogram_quantile` can actually answer
  the SLO), and `db_query_duration_seconds{query}` fed by SQLAlchemy cursor events. Label
  cardinality is bounded by construction — `route` is the templated path (resolved from the
  routing table even when a middleware short-circuits, so 401/403s are attributed to the
  endpoint they targeted), `query` is the leading SQL verb from a closed set — and requests
  that raise are counted as `500` before the exception propagates. `/metrics` excludes itself.
- OpenTelemetry tracing: FastAPI/SQLAlchemy/redis auto-instrumentation plus `<component>.<operation>`
  domain spans (`articles.*`, `auth.*`) around the service layer, yielding one trace per request
  from the HTTP server span through the domain span down to the SQL statement span. `request_id`
  is attached to spans and the span's `trace_id` is bound into the log context, joining logs and
  traces in both directions. Tracing is **off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set** and
  exports asynchronously when on, so a missing or slow collector never affects a request; adds
  `OTEL_SERVICE_NAME`.
- ADR-0009 records the observability stack: structlog + prometheus-client + OpenTelemetry,
  correlation by `request_id`, pull-based metrics vs. push-based traces, and why two span-naming
  conventions coexist.
- Alembic migrations `0001`–`0004`: schema (roles/users/articles), role seeds, and the two composite
  list indexes (`idx_articles_deleted_created`, `idx_articles_author_deleted_created`).
- Test harness and suite (acceptance/unit/integration) with an 80% coverage floor enforced via
  `pyproject.toml`.
- Project documentation scaffold (`docs/` tree).
- Repository community files and MkDocs configuration.
- Data model / ERD (`docs/data/data-model.md`) and schema & migrations guide (`docs/data/migrations.md`).
- ADR-0007 (single role per user via FK) and ADR-0008 (Alembic for schema migrations).
- `cryptography` runtime dependency — required by aiomysql/pymysql for MySQL 8 `caching_sha2_password` authentication.

[Unreleased]: https://github.com/si-sibomana/scalable-content-platform/commits/main
