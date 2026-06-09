# Non-Functional Requirements (NFR) / SLO

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-06-09

These are the *measurable* claims the system must back up. Pair with [observability/slo.md](../observability/slo.md).

**Targets vs. measured:** the **Target** column is a *requirement* set up front to drive design and to act as the pass/fail line for load testing. The **Measured** / **Status** columns stay pending until the [load-test report](../performance/load-test-report.md) (M6) produces real evidence, at which point the observed values are recorded here and status flipped to ✅/🟥.

## Performance
| Metric | Target | Measured under | Status |
|---|---|---|---|
| Read P50 latency | < 50 ms | _pending load test_ | 🟥 |
| Read P95 latency | < 200 ms | _pending load test_ | 🟥 |
| Read P99 latency | < 450 ms | _pending load test_ | 🟥 |
| Write P95 latency | < 500 ms | _pending load test_ | 🟥 |
| Throughput (sustained) | ≥ 500 req/s | _pending load test_ | 🟥 |

> **P99 matters alongside P95:** P95 can look healthy while the tail (P99) hides timeouts/GC/lock contention. Track both. Targets follow the conventional API profile (≈P50 45 ms / P95 180 ms / P99 450 ms); read P95 < 200 ms is the committed read-path target.
>
> **Throughput is a stated budget, not an industry constant** — it is workload-specific. Refine ≥ 500 req/s to the actual target load profile during M6.

## Availability & Reliability
| Metric | Target |
|---|---|
| Availability (SLO) | **99.9%** ("three nines", ~43 min/month of allowed downtime) |
| Error rate (SLO) | **< 0.1% 5xx** |
| Graceful degradation | Reads fall back to MySQL when Redis is down; load-shed / defined fallback when MySQL is impaired — see [resilience](../resilience/fault-tolerance-design.md) |

> **Error budget = 1 − SLO.** The 99.9% availability SLO and the "< 0.1% 5xx" error rate are the **same budget** expressed two ways (≈43 min/month). When the rolling-window budget is exhausted, freeze risky changes and prioritize reliability (see [observability/slo.md](../observability/slo.md) error-budget policy). 99.9% is the right MVP tier — 99.95%/99.99% require redundancy and automation beyond MVP scope.

## Scalability & Efficiency
| Metric / expectation | Target |
|---|---|
| Cache hit ratio (hot reads) | **≥ 90%** (read-heavy production band is 85–95%; this is the lever behind the read-P95 target) |
| Horizontal scaling | Stateless API scales out linearly with replicas (no sticky sessions); throughput grows with added instances |
| Connection pooling | Bounded per-instance MySQL/Redis pools sized so total connections stay within server limits as replicas scale |

> **Scope note — process vs. runtime NFRs.** The performance numbers above are *runtime* claims validated by load testing (M6). Everything **below** is a *process / design* requirement that is in force from day one and verifiable without load testing (CI gates, code review, scans, config, backups).

## Security
Secure API boundaries by default. Detailed design lives in [security/](../security/authn-authz.md); the measurable requirements:

| Requirement | Target / standard |
|---|---|
| Transport security | HTTPS only; **TLS 1.2+**; HSTS at the edge |
| Authentication | JWT access tokens, 15-min TTL — see [authn-authz](../security/authn-authz.md) ([PRD §13 D1](product-requirements.md)) |
| Authorization | RBAC enforced on **100%** of write/admin endpoints; reads public ([PRD §13](product-requirements.md)) |
| Input validation | All request bodies/params schema-validated (Pydantic); reject invalid input with `422` |
| Request size limits | Enforced max request body / field lengths; oversized payloads rejected (`422`/`413`) — bounds memory use and mitigates large-payload DoS |
| Password storage | Salted adaptive hash (bcrypt/argon2); never plaintext, returned, or logged |
| Secret handling | Env / secret store only; never committed or logged — see [secrets-management](../security/secrets-management.md) |
| Dependency vulnerabilities | **No known High/Critical CVEs** in deployed dependencies; automated scanning in CI |
| OWASP coverage | Address the **OWASP API Security Top 10**; target **ASVS Level 1+** — see [owasp-matrix](../security/owasp-matrix.md), [threat-model](../security/threat-model.md) |
| Abuse protection | Load shedding active in MVP; per-client **rate limiting deferred** ([PRD §13 D2](product-requirements.md)) |
| Audit logging | Security-relevant events (auth failures, role changes, deletes) logged with `request_id` |
| HTTP hardening | Restrictive CORS; security headers; no stack traces / internal detail in error bodies |

## Observability
Production debuggability is a first-class requirement (not load-test-dependent). Design in [observability/](../observability/observability-guide.md).

| Requirement | Target |
|---|---|
| Structured logging | JSON logs for **100%** of requests, correlated by `request_id` |
| Metrics | RED method (Rate, Errors, Duration) exported to Prometheus; latency as **P50/P95/P99** histograms |
| Tracing | OpenTelemetry end-to-end traces with **DB and cache spans**; `request_id` propagated |
| Dashboards | Grafana for latency / error rate / throughput / cache hit ratio — see [dashboards](../observability/dashboards.md) |
| Alerting | Alert on **SLO error-budget burn** and saturation — see [alerting-runbooks](../observability/alerting-runbooks.md) |

## Maintainability
| Requirement | Target |
|---|---|
| Test coverage | **≥ 80%** on core modules (Google scale: 60/75/90 = acceptable/commendable/exemplary). Coverage is a floor, not a goal — prioritize meaningful tests over the number |
| Test types | **Unit** (logic), **integration** (DB/cache via service containers), **contract** (API schema/OpenAPI), **security/abuse** (auth bypass, injection, IDOR — see [threat-model](../security/threat-model.md)), and **load** (M6). Unit/integration/contract/security run in CI; load runs in M6 |
| Lint & format | `ruff check` + `ruff format --check` clean; enforced as a **CI gate** |
| Type checking | Type hints on public interfaces; static checking encouraged |
| Code review | Required before merge via `CODEOWNERS`; CI must be green |
| Documentation | Docs-as-code kept current with changes; significant decisions captured as ADRs |

## Operability & Delivery
Delivery targets follow the **DORA "elite" tier** (2024 State of DevOps) as the north star:

| Metric | Target (DORA elite) |
|---|---|
| Deployment frequency | On-demand (multiple deploys/day capable) |
| Lead time for changes | **< 1 day** |
| Change failure rate | **≤ 5%** |
| Failed-deployment recovery (MTTR) | **< 1 hour** |

Operational requirements:

- **Health checks:** distinct liveness and readiness endpoints for Kubernetes probes — see [deployment](../operations/deployment.md).
- **Graceful shutdown:** drain in-flight requests on `SIGTERM` before exit (enables zero-downtime rollouts).
- **Configuration:** 12-factor, environment-driven; no config baked into images — see [configuration-reference](../operations/configuration-reference.md).
- **Runbooks:** documented procedures for common incidents — see [runbook](../operations/runbook.md).

## Data Management & Durability
| Requirement | Target |
|---|---|
| Backups | Automated; daily full + binlog for point-in-time recovery (PITR) |
| RPO (max data loss) | **≤ 5 min** via PITR (RPO 0 / CDP is out of scope — impractical) |
| RTO (max restore time) | **≤ 1 hour** |
| Retention | Backups retained per policy; soft-deleted articles hard-purged after the retention window by a **scheduled, idempotent, monitored** background worker that alerts on failure ([PRD §13 D3](product-requirements.md), FR-006) |
| Migrations | Versioned and reversible; **expand/contract** for zero-downtime schema change — see [migrations](../data/migrations.md) |
| Durability model | **MySQL is the source of truth**; Redis is a cache — its loss degrades latency, never correctness ([caching-strategy](../data/caching-strategy.md)) |

> RTO/RPO targets are a single production tier; real systems tier these by business impact. ≤ 5 min / ≤ 1 hr is a sensible default for the primary datastore.

## Compliance & Privacy
- **Data minimization:** store only the user data the service needs to function.
- **Right to erasure (GDPR/CCPA):** user PII is hard-deleted or anonymized on request — a soft-delete flag is **not** erasure ([PRD §13 D3](product-requirements.md)).
- **Encryption:** data encrypted in transit (TLS); secrets held in a secret store.
- **Auditability:** security-relevant actions are traceable (see Security, above).

> Design aligns with GDPR/CCPA *principles*; this is not a formal compliance certification.

## Portability & Compatibility
- **12-factor / containerized:** runs in Docker, orchestrated by Kubernetes; stateless services are portable across environments.
- **Runtime:** Python 3.11+.
- **API compatibility:** additive changes stay backward-compatible; breaking changes are versioned — see [versioning-policy](../api/versioning-policy.md).
- **Data-format conventions:** all timestamps are **UTC / ISO-8601**; request/response payloads are **UTF-8 JSON**; errors use the canonical envelope in [error-catalog](../api/error-catalog.md).

## References
- [product-requirements.md](product-requirements.md) (decisions D1–D8) · [observability/slo.md](../observability/slo.md) (SLI/SLO/error budget)
- [security/authn-authz.md](../security/authn-authz.md) · [resilience/fault-tolerance-design.md](../resilience/fault-tolerance-design.md) · [performance/load-test-report.md](../performance/load-test-report.md)
- External standards: [Google SRE — SLOs](https://sre.google/sre-book/service-level-objectives/) · [DORA State of DevOps](https://dora.dev/) · [OWASP API Security Top 10](https://owasp.org/API-Security/) · [NIST SP 800-63B](https://pages.nist.gov/800-63-3/sp800-63b.html)
