# Weekly Deliverables

> **Status:** 🟥 In progress · Tracks the 8-week schedule from `project_overview.txt`.

| Week | Theme | Deliverable | Docs produced | Status |
|---|---|---|---|---|
| 1 | Architecture & Foundations | Architecture diagram + running API skeleton | README, [PRD](../requirements/product-requirements.md), [NFR](../requirements/non-functional-requirements.md), [C4 overview](../architecture/overview.md), [ADRs](../architecture/adr/0001-record-architecture-decisions.md), [ERD](../data/data-model.md), [testing strategy](../development/testing-strategy.md) | 🟩 |
| 2 | Core API & Data | Functional content APIs | [OpenAPI](../api/openapi.md), [API ref](../api/api-reference.md), [error catalog](../api/error-catalog.md), [indexing](../data/indexing-strategy.md), [migrations](../data/migrations.md) | 🟩 |
| 3 | Auth & Security | Secured APIs w/ role enforcement | [AuthN/Z](../security/authn-authz.md), [secrets](../security/secrets-management.md), [OWASP](../security/owasp-matrix.md), [threat model](../security/threat-model.md) | 🟥 |
| 4 | Observability | Observable system + dashboards | [Observability guide](../observability/observability-guide.md), [SLO](../observability/slo.md), [dashboards](../observability/dashboards.md) | 🟥 |
| 5 | Scalability & Caching | Measurable perf improvements | [Caching](../data/caching-strategy.md), [capacity model](../architecture/capacity-scaling-model.md) | 🟥 |
| 6 | Load Testing | Load test report w/ graphs | [Plan](../performance/load-test-plan.md), [report](../performance/load-test-report.md) | 🟥 |
| 7 | Fault Injection | Resilient system under failure | [Fault tolerance](../resilience/fault-tolerance-design.md), [chaos report](../resilience/chaos-test-report.md), [alerting](../observability/alerting-runbooks.md) | 🟥 |
| 8 | Polish & Resume | Portfolio-ready backend | Final README, architecture narrative, perf tuning log, [deployment](../operations/deployment.md) | 🟥 |

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
