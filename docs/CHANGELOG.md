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
- Grafana dashboards and Prometheus rules, in-repo and provisioned: API Overview (RED + SLO and
  error-budget panels), Database (statement latency, read/write mix, statements per request, share
  of request time in the DB), and declared placeholders for Cache (M5) and Resilience (M7). Alert
  rules cover the latency and error-rate SLOs, multi-window error-budget burn rate (14.4× fast /
  6× slow), database latency, target-down, CPU saturation and zero-traffic. A consistency test
  fails the build if any panel or alert references a metric the app does not export, if a rule
  lacks `for:`/`severity`, if its `runbook_url` points at a runbook section that does not exist,
  if a label-filtered ratio panel omits the `or vector(0)` guard that keeps it from reading
  "No data" while healthy, or if a provisioned query variable would resolve to an empty option
  list. Verified against the running stack: Prometheus scraping the app, all nine rules loading,
  and every panel rendering real values in Grafana — including under injected failure, where
  availability fell to 85% and the burn-rate alert reached `firing`.
- Local observability stack behind a compose profile: `docker compose --profile observability up -d`
  adds Prometheus (9090) and Grafana (3000); the plain `docker compose up -d` the integration tests
  rely on is unchanged.
- Alembic migrations `0001`–`0004`: schema (roles/users/articles), role seeds, and the two composite
  list indexes (`idx_articles_deleted_created`, `idx_articles_author_deleted_created`).
- Test harness and suite (acceptance/unit/integration) with an 80% coverage floor enforced via
  `pyproject.toml`.
- Project documentation scaffold (`docs/` tree).
- Repository community files and MkDocs configuration.
- Data model / ERD (`docs/data/data-model.md`) and schema & migrations guide (`docs/data/migrations.md`).
- ADR-0007 (single role per user via FK) and ADR-0008 (Alembic for schema migrations).
- `cryptography` runtime dependency — required by aiomysql/pymysql for MySQL 8 `caching_sha2_password` authentication.

### Fixed
- Read-your-writes violation on every write endpoint: the request transaction committed after
  the response had been sent, so `POST /v1/auth/register` returned 201 for a row an immediately
  following `POST /v1/auth/login` could not yet see (401 in roughly three attempts out of five;
  a 300 ms pause hid it). FastAPI ends a dependency with yield after the response by default,
  and the commit is `get_session`'s teardown, so `SessionDep` now declares `scope="function"` —
  which ends it before the response leaves the router. `requirements.txt` raises the FastAPI
  floor to 0.139 accordingly, and `/health/ready` now takes the shared `SessionDep` so the
  transaction scope cannot drift per-router. Covered by an ordering test on the dependency and,
  because the in-process `ASGITransport` harness structurally cannot observe this class of bug,
  a register-then-login test against a real uvicorn socket.
- Argon2id password hashing no longer blocks the event loop. `hash_password`/`verify_password`
  were synchronous and called straight from `async def` handlers, so every register and login
  stalled the entire worker for the full hashing cost (~145 ms locally) — enough for a handful
  of concurrent logins to push unrelated cached reads past the 200 ms P95 SLO. Both are now
  coroutines that run the hash in a dedicated `ThreadPoolExecutor` (Argon2 releases the GIL, so
  the time is genuinely reclaimed). The pool is separate from the one Starlette uses for sync
  endpoints, so a login burst cannot starve them, and bounded by `PASSWORD_HASH_MAX_THREADS`
  (default 4) because unbounded offload would trade the stall for a memory blow-up at ~64 MiB
  per in-flight hash.

[Unreleased]: https://github.com/si-sibomana/scalable-content-platform/commits/main
