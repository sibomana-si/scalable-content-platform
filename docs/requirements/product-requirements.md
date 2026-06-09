# Product Requirements / Project Charter

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Version:** 0.7 · **Last updated:** 2026-06-09

This is the charter-level document for the **Scalable Content Platform Backend**. It defines the
*what*, *why*, and *for whom*. Testable detail lives in the sibling documents and is referenced rather
than duplicated here: see [Functional Requirements](functional-requirements.md) for `FR-xxx` acceptance
criteria, [Non-Functional Requirements](non-functional-requirements.md) for measurable targets/SLOs, and
the [Glossary](glossary.md) for domain terms.

## 0. Document Control

| Field | Value |
|---|---|
| Owner | Simon Sibomana |
| Status | ✅ Approved |
| Version | 0.7 |
| Last updated | 2026-06-09 |
| Reviewers / Approvers | Simon Sibomana |
| Supersedes | — |

**Change log**

| Version | Date | Author | Summary |
|---|---|---|---|
| 0.1 | 2026-06-09 | Simon Sibomana | Initial charter draft. |
| 0.2 | 2026-06-09 | Simon Sibomana | Resolved MVP open questions (token TTL, rate limiting, delete, lifecycle, ownership); propagated to FR & glossary. |
| 0.3 | 2026-06-09 | Simon Sibomana | D1 access-token TTL 30 → 15 min to align with OAuth 2.0 Security BCP (RFC 9700); refresh-token rotation noted as post-MVP. |
| 0.4 | 2026-06-09 | Simon Sibomana | D3 scoped soft delete to articles only (PII hard-delete/anonymize for GDPR), added retention + purge job; recorded read-path index impact in indexing-strategy. |
| 0.5 | 2026-06-09 | Simon Sibomana | Resolved remaining open questions D6 (password policy, NIST 800-63B), D7 (author-only list filter), D8 (Markdown, client-side render); propagated to FR-001/004/005, indexing-strategy, glossary. No charter-level questions remain open. |
| 0.6 | 2026-06-09 | Simon Sibomana | Post-review remediation: added D9 (keyset pagination), D10 (PUT full-replace), D11 (optimistic concurrency), D12 (idempotency deferred); added retention-purge worker (FR-006) to scope/dependencies; capacity assumptions + data-format conventions; expanded glossary. |
| 0.7 | 2026-06-09 | Simon Sibomana | Approved by Simon Sibomana. |

## 1. Executive Summary

The Scalable Content Platform is an **API-first, read-heavy content backend** — the kind of service that
sits behind a CMS, an internal content tool, or a SaaS content surface. It exposes a clean HTTP API for
publishing and retrieving articles, serves public read traffic at low latency, and remains observable and
resilient under load and partial failure. There is no bundled frontend; clients (web apps, mobile apps,
other services) integrate over the API.

The product optimizes for the **read path** — anonymous, high-volume article retrieval served through a
Redis cache-aside layer in front of MySQL — while keeping writes safe, authenticated, and role-controlled.

## 2. Problem Statement & Background

Teams that publish content need a backend that can serve a large volume of read traffic reliably without
coupling consumers to a particular UI. Common shortcomings in ad-hoc solutions:

- **Read traffic dominates but isn't treated as the hot path.** Caching is bolted on late, so latency and
  database load grow with readership.
- **Reliability is implicit.** When the database slows down or the cache is unavailable, the whole API fails
  instead of degrading gracefully.
- **Operability is an afterthought.** Without structured logs, metrics, and traces, diagnosing a slow or
  failing endpoint in production is guesswork.
- **Security boundaries are inconsistent.** Authentication and authorization are applied unevenly across
  endpoints.

This product addresses those gaps directly: an explicit, instrumented, cache-first read path; a secure,
role-aware write path; and first-class observability and resilience.

## 3. Product Vision & Goals

**Vision:** a dependable content API that is fast for readers, safe for authors, and transparent for operators.

| # | Goal | How it's measured (see [NFR](non-functional-requirements.md)) |
|---|---|---|
| G1 | Serve public content reads fast under load | Read-path **P95 < 200 ms** at the target load profile |
| G2 | Keep writes secure and role-controlled | 100% of write/admin endpoints enforce JWT + RBAC |
| G3 | Stay available under partial failure | Graceful degradation when cache or DB is impaired; error-rate SLO held |
| G4 | Scale horizontally with demand | Stateless API instances scale out without sticky sessions |
| G5 | Be debuggable in production | Every request traceable end-to-end (logs, metrics, traces correlated by request ID) |

Exact numeric targets are owned by [non-functional-requirements.md](non-functional-requirements.md) and
[observability/slo.md](../observability/slo.md) — this document links to them so there is a single source of truth.

## 4. Non-Goals / Out of Scope

Explicitly **not** in scope for this product. These boundaries prevent scope creep and signal deliberate focus:

- **No frontend / UI** — API only; no server-rendered pages or admin web console.
- **No rich-text / WYSIWYG editor** — article bodies are stored/served as provided (e.g. text/Markdown); rendering is the client's concern.
- **No media / asset pipeline** — no image/video upload, transcoding, or CDN management.
- **No full-text search engine** — list endpoints support filtering and pagination, not relevance-ranked search (no Elasticsearch/OpenSearch).
- **No multi-tenancy** — single logical tenant; no per-org data isolation.
- **No social features** — no comments, likes, follows, or feeds.
- **No notification system** — no email, SMS, or push (including no email verification or password-reset email flows in the MVP).
- **No billing / payments / quotas-as-a-product.**
- **No third-party / federated login** (OAuth social sign-in, SAML) — local credentials + JWT only.

## 5. Target Users / Personas

| Persona | Description | Needs | Key interactions |
|---|---|---|---|
| **Anonymous Reader** | Unauthenticated consumer (public site, mobile app, integrator). The highest-volume actor. | Fast, reliable article retrieval; stable pagination/filtering. | `GET` article by id; `GET` paginated/filtered article lists. No auth required. |
| **Registered User / Author** | An authenticated principal who creates and manages their own content. | Account + login; ability to author and edit their own articles. | Register; log in (JWT); create/update/delete **own** articles; read like anyone. |
| **Administrator** | Authenticated principal with elevated privileges. | Manage users/roles and moderate any content. | Manage user roles; create/update/delete **any** article; all author capabilities. |
| **Operator / SRE** | Internal stakeholder running the service (not an API role). | Visibility and control to keep SLOs green. | Consumes dashboards/metrics/traces and [runbooks](../operations/runbook.md); operates deploys and incident response. |

The API authorization roles are **`user`** and **`admin`** (see [RBAC](../security/authn-authz.md)); the
*Anonymous Reader* and *Operator / SRE* personas are not API roles but are first-class product audiences.

## 6. Scope — Capabilities (MVP)

High-level capabilities in scope. Each maps to detailed acceptance criteria in
[functional-requirements.md](functional-requirements.md); summaries here, not restatements.

| Capability | Summary | Primary access | Ref |
|---|---|---|---|
| Account registration | Create a user account with validated input | Public | FR-001 |
| Authentication (JWT) | Log in for a short-lived access token | Public → authenticated | FR-002 |
| Authorization (RBAC) | `user` vs `admin` enforced via middleware | Authenticated | FR-003 |
| Article CRUD | Create/read/update/delete articles; authors own their articles, admins override | Authenticated writes | FR-004 |
| Public reads | Retrieve articles individually and as paginated, filtered lists | **Anonymous** | FR-005 |
| Caching | Cache-aside on hot reads with invalidation on write | Internal | [caching-strategy](../data/caching-strategy.md) |
| Observability | Structured logs, metrics, and traces correlated by request ID | Internal | [observability](../observability/observability-guide.md) |
| Resilience | Timeouts, retries with backoff, circuit breakers, graceful fallback | Internal | [fault-tolerance](../resilience/fault-tolerance-design.md) |
| Retention purge (scheduled) | Background worker hard-purges articles soft-deleted past the retention window | Internal (worker) | FR-006 |

**Access boundary (confirmed):** article **reads are public** (no authentication); **writes and admin
actions require authentication and the appropriate role.**

## 7. Key Use Cases / User Journeys

1. **Reader retrieves an article (hot read path).** Anonymous client requests an article → API checks Redis →
   on hit, returns cached payload; on miss, reads MySQL, populates cache, returns. This is the
   latency-critical journey behind G1. (See the read-path diagram in [architecture/overview.md](../architecture/overview.md).)
2. **Reader browses a list.** Anonymous client requests a paginated, filtered article list with stable ordering.
3. **Author publishes content.** Visitor registers → logs in (receives JWT) → creates an article → the write
   persists to MySQL and **invalidates** the relevant cache entries so readers see fresh content.
4. **Author edits/removes own content.** Authenticated author updates or deletes an article they own; attempts to
   modify another user's article are rejected by RBAC.
5. **Admin manages users and content.** Admin promotes/demotes a user's role and can edit or remove any article.
6. **Graceful degradation.** When Redis is unavailable, reads fall back to MySQL within timeout budgets; when MySQL
   is impaired, the API sheds load / serves a defined fallback rather than hanging or cascading. (See
   [fault-tolerance-design.md](../resilience/fault-tolerance-design.md).)

## 8. Success Criteria & KPIs

The product is successful when these outcomes hold under the target load profile. Exact thresholds are owned by
[non-functional-requirements.md](non-functional-requirements.md) and [observability/slo.md](../observability/slo.md).

| KPI | Outcome we want | Signal |
|---|---|---|
| Read latency | Reads stay fast under load | Read-path **P95 < 200 ms** (also track P50) |
| Error rate | Failures are rare | 5xx rate within SLO |
| Availability | Service is dependable | Availability SLO met |
| Cache effectiveness | DB is shielded from read load | Cache hit ratio at/above target |
| Graceful degradation | Partial outages don't become total | Defined fallback behavior verified under fault injection |
| Security posture | Boundaries hold | No unauthorized write/admin access in tests; OWASP checks pass |
| Horizontal scalability | Capacity grows with instances | Throughput scales with added stateless replicas |

## 9. Constraints & Assumptions

**Constraints (committed; no substitutions per the design brief)**

- **Stack:** Python + FastAPI, MySQL, Redis, Docker, Kubernetes.
- **MySQL is the single source of truth;** Redis is a cache only (it may be cold/empty at any time).
- **Services are stateless** — no in-process session state; anything durable lives in MySQL/Redis.
- **Secrets via environment / secret store** — never committed (see [secrets-management](../security/secrets-management.md) and `.env.example`).
- **Auth model:** local credentials + JWT access tokens (**15-minute TTL, no refresh token in MVP**); RBAC roles `user` and `admin`.
- **Article lifecycle:** articles are **published on create** (no draft state) and use **soft delete**; **authorship is immutable** (see §13).

- **Data-format conventions:** timestamps are **UTC / ISO-8601**, payloads are **UTF-8 JSON**, and all errors use the canonical envelope (defined in [non-functional-requirements](non-functional-requirements.md) and [error-catalog](../api/error-catalog.md)).

**Assumptions**

- **Article reads are public**; only writes/admin actions are authenticated.
- **Single region, single tenant** for the MVP.
- Workload is **read-heavy** (reads vastly outnumber writes), which justifies the cache-first design.
- Article bodies are bounded text (not large binary/media payloads).
- **Capacity (order-of-magnitude, to validate in M6):** ~1k registered users, ~10k articles, read:write ≈ **100:1**, peak ≈ **500 req/s**. These are planning assumptions, not commitments — refine against the [capacity & scaling model](../architecture/capacity-scaling-model.md) and load tests.

## 10. Dependencies

| Dependency | Role | Posture on failure |
|---|---|---|
| MySQL | Source of truth (users, roles, articles) | Hard dependency for writes; reads degrade/fall back within timeout budgets |
| Redis | Cache-aside + load shedding | Soft dependency — on outage, reads fall through to MySQL |
| OpenTelemetry collector | Trace/metric export | Best-effort — telemetry loss must not break request handling |
| Prometheus / Grafana | Metrics storage + dashboards | Operator-facing; no impact on request path |
| Background worker / scheduler (purge job) | Runs the retention hard-purge (FR-006) | Off the request path; idempotent and retryable — failure delays purge only, never affecting reads/writes |

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Cache stampede on hot-key expiry | DB load spike, latency breach | TTL jitter, single-flight/locking, load shedding ([caching-strategy](../data/caching-strategy.md)) |
| DB read hotspots | P95 regression | Read-optimized indexing, avoid over-normalization ([indexing-strategy](../data/indexing-strategy.md)) |
| Auth bypass / privilege escalation | Data integrity, security | RBAC middleware on all write/admin routes + negative tests ([authn-authz](../security/authn-authz.md), [threat-model](../security/threat-model.md)) |
| Cascading failure from a slow dependency | Outage | Explicit timeouts, retries with backoff, circuit breakers ([fault-tolerance-design](../resilience/fault-tolerance-design.md)) |
| Stale reads after a write | Incorrect content served | Cache invalidation on write; bounded TTLs |
| Secret leakage | Compromise | Env/secret-store only; `.gitignore` excludes `.env`; secrets never logged |

## 12. Release Plan / Milestones

Delivered as incremental product increments, each independently demonstrable:

| Increment | Focus | Exit criteria |
|---|---|---|
| M1 — Foundations | Project skeleton, MySQL schema, architecture docs | Running API skeleton + architecture diagram |
| M2 — Core content APIs | Article CRUD, pagination/filtering, transactions, indexing | Functional content APIs (public reads, authed writes) |
| M3 — Auth & Security | JWT auth, RBAC middleware, input validation, secret handling | Secured APIs with role enforcement |
| M4 — Observability | Structured logging, Prometheus metrics, Grafana, OTEL tracing | Observable system with dashboards |
| M5 — Caching & Scale | Redis cache-aside, invalidation, connection pooling, scale-out | Measurable read-path improvement |
| M6 — Load validation | Load tests (k6/Locust), bottleneck analysis | [Load-test report](../performance/load-test-report.md) against P95 target |
| M7 — Resilience | Fault injection, timeouts/retries/circuit breakers, fallbacks | Defined behavior under DB/cache failure |
| M8 — Launch readiness | Documentation polish, dashboards, design narrative | Production-ready, documented release |

## 13. Decisions & Open Questions

**Resolved (MVP decisions)** — these are propagated to [functional-requirements.md](functional-requirements.md) and the [glossary](glossary.md):

| # | Question | Decision | Rationale |
|---|---|---|---|
| D1 | Token lifetime & refresh | **15-minute access token; no refresh token** in MVP (re-authenticate on expiry) | Short TTL bounds the blast radius of a non-revocable bearer token (aligns with the OAuth 2.0 Security BCP, RFC 9700). Refresh-token **rotation** is the documented post-MVP path. |
| D2 | Rate limiting on public reads | **Deferred** post-MVP; rely on the in-scope **load shedding** for protection | Per-client rate limiting adds infra (identity, counters) out of step with MVP scope. |
| D3 | Article delete semantics | **Soft delete for articles only** (`deleted_at` timestamp; excluded from reads), with a **retention window then a scheduled hard-purge** job. **PII (users) is hard-deleted/anonymized**, not soft-deleted. | Soft delete gives content recoverability + audit and plays cleanly with cache invalidation; a purge job bounds table/index growth; PII must be truly erasable for GDPR/CCPA ("right to erasure"), so a flag is insufficient there. Read-path index impact noted in [indexing-strategy](../data/indexing-strategy.md). |
| D4 | Article lifecycle | **Published on create** — no draft/publish state in MVP | Matches the read-heavy, single-content-state model; a `status` field can be added later. |
| D5 | Ownership transfer | **Out of scope** — authorship is immutable; admins may edit/delete any article but not reassign it | Avoids an authorization edge case with no MVP user need. |
| D6 | Password policy | **NIST SP 800-63B, length-first:** minimum 12 characters, no forced composition rules, no periodic rotation; screen new passwords against a known-breached list | Length beats complexity for entropy and usability; composition/rotation rules are discouraged by current NIST guidance. Drives FR-001 validation. |
| D7 | List filtering surface | **Filter by `author` only** in MVP (plus the implicit `deleted_at IS NULL`); date-range and other fields deferred until load tests justify the index | Keeps the index footprint minimal on the read-heavy path; expand only with evidence. Drives FR-005 + [indexing-strategy](../data/indexing-strategy.md). |
| D8 | Article body format | **Store raw, render client-side; Markdown is the documented convention.** Server does no rendering (input validation only) | Matches the "no WYSIWYG / no server-side rendering" Non-Goals (§4); keeps the API a content store, not a renderer. |
| D9 | Pagination strategy | **Keyset/cursor on `(created_at, id)`** (id tiebreaker), bounded page size (enforced default + max) | Constant cost at any page depth on the read-heavy path; matches the `(deleted_at, created_at)` indexes and the "avoid unbounded OFFSET" rule in [indexing-strategy](../data/indexing-strategy.md). Drives FR-005. |
| D10 | Update semantics | **`PUT` full-replace** of the article resource in MVP; `PATCH` (partial) deferred | Simplest, most predictable contract for an MVP. Drives FR-004. |
| D11 | Write concurrency | **Optimistic concurrency control** on update/delete via an `updated_at`/version precondition (`If-Unmodified-Since`/`If-Match`); a stale write returns `409` | Cheap protection against lost updates; satisfies the brief's "transactional consistency" without pessimistic locking. Drives FR-004 + [error-catalog](../api/error-catalog.md). |
| D12 | Create idempotency | **Deferred** post-MVP; an `Idempotency-Key` header is the reserved future path | Duplicate-submission protection adds storage/lookup overhead not justified at MVP write volumes; documented so it can be added without a breaking change. |

**Still open**

_None — all charter-level questions are resolved. Remaining TBDs are measurement-driven (exact SLO numbers, pending load testing — see [non-functional-requirements.md](non-functional-requirements.md)) or sign-off (reviewers/approvers in §0)._

## 14. References

- [Functional Requirements](functional-requirements.md) · [Non-Functional Requirements](non-functional-requirements.md) · [Glossary](glossary.md)
- [Architecture overview](../architecture/overview.md) · [API reference](../api/api-reference.md)
- [AuthN/AuthZ](../security/authn-authz.md) · [Caching strategy](../data/caching-strategy.md) · [Observability SLOs](../observability/slo.md) · [Fault-tolerance design](../resilience/fault-tolerance-design.md)
