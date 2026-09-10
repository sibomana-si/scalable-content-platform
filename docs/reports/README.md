# Weekly Deliverables

> **Status:** 🟩 Complete

| Week | Theme | Deliverable | Docs produced | Status |
|---|---|---|---|---|
| 1 | Architecture & Foundations | Architecture diagram + running API skeleton | README, [PRD](../requirements/product-requirements.md), [NFR](../requirements/non-functional-requirements.md), [C4 overview](../architecture/overview.md), [ADRs](../architecture/adr/0001-record-architecture-decisions.md), [ERD](../data/data-model.md), [testing strategy](../development/testing-strategy.md) | 🟩 |
| 2 | Core API & Data | Functional content APIs | [OpenAPI](../api/openapi.md), [API ref](../api/api-reference.md), [error catalog](../api/error-catalog.md), [indexing](../data/indexing-strategy.md), [migrations](../data/migrations.md) | 🟩 |
| 3 | Auth & Security | Secured APIs w/ role enforcement | [AuthN/Z](../security/authn-authz.md), [secrets](../security/secrets-management.md), [OWASP](../security/owasp-matrix.md), [threat model](../security/threat-model.md) | 🟩 |
| 4 | Observability | Observable system + dashboards | [Observability guide](../observability/observability-guide.md), [SLO](../observability/slo.md), [dashboards](../observability/dashboards.md), [alerting](../observability/alerting-runbooks.md), [ADR-0009](../architecture/adr/0009-observability-stack.md) | 🟩 |
| 5 | Scalability & Caching | Measurable perf improvements | [Caching](../data/caching-strategy.md), [capacity model](../architecture/capacity-scaling-model.md), [ADR-0010](../architecture/adr/0010-generation-counter-list-invalidation.md) | 🟩 |
| 6 | Load Testing | Load test report w/ graphs | [Plan](../performance/load-test-plan.md), [runbook](../performance/load-test-runbook.md), [bottleneck analysis](../performance/bottleneck-analysis.md), [report](../performance/load-test-report.md), [ADR-0011](../architecture/adr/0011-k6-for-load-testing.md) | 🟩 |
| 7 | Fault Injection | Resilient system under failure | [Fault tolerance](../resilience/fault-tolerance-design.md), [chaos runbook](../resilience/chaos-test-runbook.md), [chaos report](../resilience/chaos-test-report.md), [ADR-0012](../architecture/adr/0012-timeout-retry-and-circuit-breaker-policy.md), [alerting](../observability/alerting-runbooks.md) | 🟩 |
| 8 | Polish | Portfolio-ready backend | Root README, [system design narrative](../architecture/system-design-narrative.md), [overview refresh](../architecture/overview.md) with its "what the measurements changed" log, [dashboard screenshots](../observability/dashboards.md) | 🟩 |

From Week 2 onward, each functional deliverable is evidenced by its **green FR acceptance suite** in CI
(e.g. Week 2's content APIs = FR-004/FR-005 tests passing) — see the
[testing strategy](../development/testing-strategy.md) and
[ADR-0006](../architecture/adr/0006-test-driven-development.md).

Add a dated entry below per week summarizing what was completed, metrics captured, and links to evidence.

## Log
- 2026-07-18 — Week 1 — Foundations delivered: walking skeleton (`create_app()` factory, lazy
  async DB/Redis clients, `/health/live` + `/health/ready`), documentation tree, ADRs, C4 overview,
  and the TDD test harness with an 80% coverage floor.
- 2026-07-18 — Week 2 — Core content APIs delivered: article CRUD (FR-004) and keyset
  pagination + `author` filter (FR-005) under `/v1/articles`, transaction-per-request with
  compare-and-set optimistic concurrency (`If-Match`/409), canonical error envelope, dev
  `X-User-Id` auth stub, and Alembic migrations 0001–0004 (schema, role seeds, two composite list
  indexes). Evidenced by the green FR-004/FR-005 acceptance suites in CI.
- 2026-08-01 — Week 3 — Auth and security delivered: JWT registration/login (FR-001/FR-002) with
  Argon2id hashing and timing-equalized, non-enumerating 401s; token verification and RBAC
  enforcement in `AuthMiddleware` over a pure, unit-tested route policy (FR-003, 401 before 403);
  registration input validation with breached-password screening. Replaces the `X-User-Id` dev
  stub. Evidenced by the green FR-001/FR-002/FR-003 acceptance suites.
- 2026-08-01 — Week 4 — Observability delivered: structured JSON logging correlated by
  `request_id` (which also finally feeds the error envelope's `request_id`), Prometheus RED
  metrics at `/metrics` with unit-tested cardinality guards, OpenTelemetry tracing (off unless a
  collector is configured) giving server → domain → SQL spans in one trace, and four provisioned
  Grafana dashboards plus alert rules on the SLO error-budget burn rate, runnable locally via
  `docker compose --profile observability up -d`. Recorded in
  [ADR-0009](../architecture/adr/0009-observability-stack.md). The Cache and Resilience dashboards
  ship as declared placeholders — their metrics land in M5 and M7.
- 2026-08-21 — Week 5 — Scalability and caching delivered: Redis cache-aside on both article read
  paths, under jittered TTLs and a `SET NX` single-flight lock; generation-counter list
  invalidation that reaches every affected page with one `INCR`, no `SCAN` and no key registry
  ([ADR-0010](../architecture/adr/0010-generation-counter-list-invalidation.md)); invalidation
  queued and drained after the commit rather than inline, so no reader can repopulate the cache
  from a pre-commit row; bounded MySQL and Redis pools (`DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`,
  `DB_POOL_RECYCLE`, `REDIS_MAX_CONNECTIONS`) behind a `db_pool_connections{state}` gauge; and the
  application Docker image with a `scale` compose profile. Stateless scale-out is proven twice: two
  `create_app()` instances over one MySQL and one Redis in CI, and three real replicas behind an
  nginx balancer under the `scale` marker. The Cache dashboard replaces its M5 placeholder, and
  `tests/unit/test_capacity_model.py` runs the capacity model's own arithmetic against the shipped
  defaults, so the document and the code cannot drift.
- 2026-08-28 — Week 6 — Load testing and optimization delivered: k6 scenarios (`steady`, `ramp`,
  `spike`) behind the `load` compose profile, drawing 80% of reads from a 20% hot set, over a
  10,000-article seeded dataset; `scripts/run_load_matrix.sh` drives runs A, B, C and a repeat, and
  `scripts/perf_env.sh` holds and records the machine state around each one
  ([ADR-0011](../architecture/adr/0011-k6-for-load-testing.md)). Three replicas held **525 req/s
  for five minutes** at a read P95 of **10.9 ms**, a write P95 of **26.2 ms**, and zero errors, so
  the five performance rows in
  [non-functional-requirements.md](../requirements/non-functional-requirements.md) now carry
  measured values. One replica bends near **450 req/s** and three near **900**, so throughput grows
  with replicas but not in proportion to them. The measured noise floor is **4.4% on read P95**,
  which the report states in its summary: a change under about 7% is drift, not a result. One
  optimization shipped — list pages no longer carry `body`, cutting a page of 20 items from 101,367
  bytes to 2,935 — and it moved latency, not the ceiling. The runs also corrected the capacity
  model: the blended hit ratio is 72.3%, not the assumed 90%, because list pages hit under 10% of
  the time. Finding F5 stayed open here — past the knee the client loses requests at connection
  level that no server-side counter sees — and M7 closes it with load shedding. See
  [load-test-report.md](../performance/load-test-report.md).
- 2026-08-29 — Week 7 — Fault injection and resilience delivered: a Toxiproxy fault injector
  (`scripts/inject_fault.py`) behind the `chaos` compose profile; timeouts, retries with full
  jitter and circuit breakers composed in one order in `app/resilience/guard.py`
  ([ADR-0012](../architecture/adr/0012-timeout-retry-and-circuit-breaker-policy.md)); graceful
  fallback answering 504 `UPSTREAM_TIMEOUT` and 503 `SERVICE_UNAVAILABLE` with `Retry-After`
  instead of hanging; load shedding at 90 requests in flight, derived from the M6 knee by Little's
  law; degraded readiness that keeps a replica in the pool when only Redis is down; and a
  Resilience dashboard with four alerts. Validated by five chaos experiments plus a repeat, under a
  5-minute k6 steady run each — see [chaos-test-report.md](../resilience/chaos-test-report.md).
  The experiments found one defect: a refused MySQL connection answered 500 and never opened the
  breaker, because the retry's rollback closed the request transaction. Fixed in
  `app/db/session.py` and re-tested. Redis blackholed cost zero failures and 0.7 ms of P95; MySQL
  blackholed still served half the read traffic from cache and refused the rest in under 5 ms.
- 2026-09-03 — Week 8 — Polish delivered: a root `README.md` that restates the five
  measured performance rows from the NFR with their evidence links, one Mermaid container diagram
  and the API Overview screenshot; the [architecture overview](../architecture/overview.md)
  refreshed to the built system, with a new §6 on what the measurements changed; four Grafana
  screenshots captured over the retained 2026-08-29 chaos series by `scripts/capture_dashboards.sh`
  and placed in the [dashboards catalog](../observability/dashboards.md); and the
  [system design narrative](../architecture/system-design-narrative.md) that tells the story
  from requirement to measurement. Open gaps stay named in the narrative:
  no Kubernetes manifests, four pages in progress, one-host measurements.
