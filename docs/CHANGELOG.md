# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- `docs/performance/bottleneck-analysis.md` records what the four-run matrix of 2026-08-22
  measured, ranked by cost. The headline is that **one app replica saturates near 430 rps and the
  cache does not raise that ceiling**: the cached knee (434 rps) and the uncached knee (429 rps)
  fall in the same rate step, while three replicas carry 509 rps at a P95 of 16.3 ms with no bend.
  The limit is CPU inside the application, not MySQL — query P95 holds at 0.96–1.00 ms from 200 rps
  to 509 rps — and not Redis, which logged zero errors. The largest lever on that CPU is the list
  endpoint: one page of 20 items is 101,367 bytes, of which **96.7% is article body text**, and the
  list route costs 4.4x the detail route at every load level in all four runs.
- Two measurements that contradict a written assumption. The list cache serves **9.5% of list
  reads** against 82% for articles, so the blended 68% describes neither half — the accepted cost of
  the [ADR-0010](architecture/adr/0010-generation-counter-list-invalidation.md) generation counter, now with
  a number. And the ≥ 90% hit ratio target cannot hold under the 80/20 skew the plan specifies: the
  cold tail caps the achievable ratio near 82%. The target moves to fit the workload, not the other
  way round.
- `docs/architecture/capacity-scaling-model.md` gains a "What the M6 matrix found" table against
  B1–B6. B1, B2 and B4 did not appear. B3 appeared, but on one replica with the cache off rather
  than at the predicted 8+ replicas. The limit that arrives first is not on the list.
- The load test runbook is complete: a full matrix costs **68.5 minutes**, and the two failures met
  during the run — a matrix stopped by a crossed threshold, and dropped iterations caused by shell
  commands run during a window — are written down with their fixes.

- k6 load tests behind a `load` compose profile (M6). `tests/load/` holds three open-model
  scenarios — `steady.js` at a constant arrival rate with the NFR targets as thresholds, `ramp.js`
  to find the knee, and `spike.js` to measure degradation and recovery. All three are arrival-rate
  executors, never a virtual-user count: a closed model backs off when the server slows, so
  throughput flattens into a line that hides saturation instead of showing it. The NFR numbers live
  once, in `tests/load/lib/slo.json`, and `tests/unit/test_load_profile.py` fails when they drift
  from `non-functional-requirements.md`. [ADR-0011](architecture/adr/0011-k6-for-load-testing.md)
  records why k6 and not Locust: the generator shares a laptop with the system under test, so
  generator CPU cost is a term in the measurement error, and the k6 service is pinned with
  `cpuset: "12-19"` to leave all six P-cores to the service.
- The read workload draws article ids from a hot set — 20% of the rows take 80% of the reads —
  because the ≥ 90% cache hit ratio target holds only under that skew. A uniform draw across 10,000
  rows against a 300-second TTL measures a cache that cannot work, and would "prove" the design
  fails when the workload was wrong. `tests/load/selftest.js` asserts the picker over 100,000 draws
  in about a second, with no infrastructure, so a distribution bug fails before a five-minute run
  builds a result on top of it.
- `scripts/seed_load_dataset.py` writes the 10,000-article dataset through the SQLAlchemy models in
  batches, idempotently: a second run tops the count up rather than duplicating. Seeding through
  the API would be slow, would pollute the metrics about to be read, and would give no control over
  the author distribution. The decision logic is pure, so `tests/unit/test_seed_dataset.py` checks
  the batching, the body bounds, and the skew in milliseconds; `tests/integration/test_seed_dataset.py`
  confirms all three survive a round trip through real MySQL. Load-test accounts use the reserved
  `loadtest.example` domain and an unusable password hash, so none of them can be logged into.
- Machine state is now a recorded part of every run, not an assumption. `scripts/perf_env.sh` locks
  the power profile with `powerprofilesctl` — not `cpupower`, because `power-profiles-daemon`
  reverts a raw `sysfs` write mid-run, and the run still produces numbers — and emits a JSON
  snapshot of the governor, the energy performance preference, the RAPL limits, both throttle
  counters, the generator `cpuset`, and the AC state. `scripts/run_metadata.py` validates that
  snapshot and decides whether a before-and-after pair is citable, reporting every reason it is not
  rather than the first. `scripts/run_load_matrix.sh` drives the four-run matrix and restores the
  daily power profile from an EXIT trap, so an interrupted matrix never leaves the laptop pinned.
- Prometheus now scrapes each app replica directly, through `dns_sd_configs` on the compose service
  name, so the job grows and shrinks with `--scale app=N`. The previous configuration scraped only
  the host job, which meant a three-replica run had no server-side metrics at all. Scraping through
  nginx would have been worse than nothing: round-robin folds three replicas' counters into one
  series that looks plausible and is wrong. `tests/unit/test_dashboards.py` asserts the job exists,
  targets port 8000, and never names nginx.
- `docs/performance/load-test-runbook.md` — how to repeat the measurement on a cold machine, with
  the prerequisites, the matrix, results collection, the citability checks, teardown, and a
  parameter reference. `tests/unit/test_load_runbook.py` parses its command blocks and fails when a
  script path, a compose profile, a k6 argument, or an environment variable stops resolving, so the
  document cannot rot into a set of commands that no longer run.

- Horizontal scale-out, proven two ways (M5). `tests/integration/test_horizontal_scaling.py`
  runs two `create_app()` instances against one MySQL and one Redis and asserts the
  application-level properties: a write on one instance is readable on the other, the cache is
  shared rather than per-process, invalidation crosses the process boundary, a JWT minted by one
  instance is accepted by the other, a cursor issued by one is honored by the other, and an
  alternating request sequence matches running it all on one instance. It runs in CI on every
  push. `tests/integration/test_multi_replica.py` re-runs the same properties across real
  containers behind an nginx round-robin balancer, under a new `scale` marker that skips unless
  `localhost:8080` answers.
- Application Docker image and a compose `scale` profile. Multi-stage build on `python:3.11-slim`
  to match the CI interpreter, non-root `appuser`, runtime dependencies only but pinned to
  `requirements.lock` through a constraints file, and a `HEALTHCHECK` on the dependency-free
  `/health/live`. `.dockerignore` keeps `.env`, `.venv/`, `.git/`, `tests/` and `docs/` out of the
  build context. `docker compose --profile scale up -d --build --scale app=3` adds the replicas
  and the balancer; a plain `docker compose up -d` is still exactly MySQL and Redis, which is what
  the integration suite needs.
- `/health/live` now reports an `instance` id, generated once per process, so the multi-replica
  test can count distinct replicas through the balancer. It is on the probe and nowhere else:
  infrastructure detail does not belong on the data path.
- Bounded MySQL and Redis connection pools (M5). The engine set `pool_size` and nothing else, so
  overflow, the pool wait, and connection recycling all ran on library defaults while the capacity
  model's replica-ceiling arithmetic already assumed 10/5. Adds `DB_MAX_OVERFLOW` (5),
  `DB_POOL_TIMEOUT` (10s) and `DB_POOL_RECYCLE` (1800s), plus `REDIS_MAX_CONNECTIONS` (50) because
  redis-py grows its pool without limit. Every bound makes a failure bounded: an unbounded pool
  wait turns one slow query into a total stall, since every later request queues behind it and
  nothing ever fails, so nothing ever alerts. The settings builders reject the unbounded forms
  outright — a zero `pool_timeout`, a negative `max_overflow` — because SQLAlchemy reads both as
  "no limit", so a typo in an env var would silently remove the ceiling. New
  `db_pool_connections{state}` gauge, sampled at scrape time rather than per request, with pool
  and utilization panels on the Database dashboard. `tests/unit/test_capacity_model.py` runs the
  document's own arithmetic against the shipped defaults, so the two cannot drift.
- Cache invalidation on write (M5): a create advances both list generations, and an update or
  delete also drops the cached article body. The author whose counter moves is the article's, not
  the actor's, so an admin editing someone else's article invalidates the right pages. A write by
  one author leaves every other author's cached pages addressable.
  **Invalidation runs after the commit, never inline.** `get_session` commits in its teardown, so
  an inline `DEL` would run before the row was durable: a concurrent reader could miss, read the
  pre-commit row, and repopulate the cache with the old value, which would then survive its full
  TTL with no error and no metric. Writes now register their invalidation on a per-session queue
  (`app/db/after_commit.py`) that `get_session` drains once the transaction block exits cleanly,
  and discards on rollback — so a 403, a 409, or any raised handler invalidates nothing.
  Invalidation is fail-open: the commit already succeeded, so a Redis failure is counted and
  logged, and the client still gets its 201.
- Redis cache-aside on the article read path (M5): `GET /v1/articles/{id}` and the list endpoint
  check Redis first and populate it on a miss, under a TTL with jitter (300s for a body, 60s for
  a page, +/- 20%). List page keys embed a generation counter — one global, one per author — so a
  write invalidates every page it can affect with a single `INCR`, with no `SCAN` and no key
  registry (ADR-0010). Concurrent misses on one key are coalesced by a `SET NX` single-flight
  lock, and a loser that waits out its budget reads MySQL itself rather than hanging.
  Authorization and compare-and-set never read the cache: `ArticleService.get` is cache-aside and
  `_load` always reads MySQL, so a stale `author_id` cannot decide ownership and a stale
  `updated_at` cannot become the CAS token. The cache is an optimization and never a dependency —
  every operation catches `RedisError`/`TimeoutError`, counts it, and degrades — and one failure
  latches the rest of that request's cache work off, so a blackholed Redis costs one socket
  timeout rather than four (8s -> 2s, measured). `CACHE_ENABLED=false` bypasses Redis entirely.
  New metrics `cache_hits_total{entity}`, `cache_misses_total{entity}` and
  `cache_errors_total{operation}`, all on closed label sets, plus the Cache Grafana dashboard
  that replaces the M5 placeholder.
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
- `/health/ready` can no longer cause the outage it reports. It ran its `SELECT 1` inside the
  request transaction, so the pooled connection stayed checked out until the end of the request
  — across the Redis probe, which was unbounded (`redis.asyncio` defaults `socket_timeout` to
  `None`). Against a reachable-but-unresponsive Redis every probe pinned a connection
  indefinitely, and since Kubernetes retries on a timer while uvicorn does not cancel the
  handler on client disconnect, they accumulated until the pool (10 + 10 overflow) was gone and
  real traffic blocked for `pool_timeout`; a 30-second hang was then reported as `200 ready`.
  Readiness now takes its own short-lived AUTOCOMMIT connection, released before Redis is
  touched, and bounds each check with `READINESS_TIMEOUT_SECONDS` (default 2s) so a hung
  dependency becomes a prompt 503 rather than an accumulating orphan. The shared Redis client
  gained `REDIS_SOCKET_TIMEOUT` / `REDIS_SOCKET_CONNECT_TIMEOUT` (default 2s each), which the
  M5 cache-aside path inherits.
- `/health/live` and `/health/ready` are excluded from the RED metrics, as `/metrics` already
  was. Probe traffic was diluting the availability ratio and the error budget with requests no
  user sent, and it held the denominator of `NoTrafficReceived` permanently above zero — an
  alert that could therefore never fire in the deployment it exists for.
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
