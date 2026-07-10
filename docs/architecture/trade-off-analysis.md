# Trade-off Analysis

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-10

Explicit "we chose X, accepting cost Y, rejecting Z because…" reasoning. For single, weighty decisions
the canonical record is an [ADR](adr/0001-record-architecture-decisions.md); product-level decisions
carry a PRD reference ([§13 D1–D12](../requirements/product-requirements.md)). This document is the
cross-cutting narrative that shows how the trade-offs **compose**.

## Decision table

| Decision area | Chosen approach | Accepted cost | Rejected alternative | Rationale | Record |
|---|---|---|---|---|---|
| Service topology | Modular monolith with service boundaries | Coarser deploy unit; one blast radius for code defects | True microservices | One stateless deployable scales horizontally just as well, without per-service CI/CD, service discovery, and distributed-failure modes; module seams (auth/content) preserve the future split lines | [ADR-0002](adr/0002-modular-monolith.md) |
| Primary datastore | MySQL | No transactional DDL; fewer advanced types than PostgreSQL | PostgreSQL | Committed by the project brief; fully adequate for a relational, read-heavy CRUD model — the design (indexing, keyset pagination, pooling) is what carries the NFRs, not the engine choice | [ADR-0003](adr/0003-mysql-source-of-truth.md) |
| Caching pattern | Cache-aside (lazy load + invalidate on write) | Bounded stale window (TTL); first-read miss penalty; stampede risk to mitigate | Write-through / read-through | Cache stays an optimization, never a dependency: Redis may be cold or absent at any time and MySQL remains correct — which is exactly the graceful-degradation requirement | [ADR-0004](adr/0004-redis-cache-aside.md) |
| Auth | Stateless JWT access tokens | No immediate revocation (mitigated by 15-min TTL); token size on every request | Server-side sessions | No session store on the hot path; any replica validates any token — the property horizontal scaling depends on | [ADR-0005](adr/0005-stateless-jwt-auth.md) |
| Token lifetime | 15-min TTL, **no refresh token** in MVP | Clients re-authenticate every 15 min | Long-lived tokens; refresh-token flows | Short TTL bounds the blast radius of a non-revocable bearer token (RFC 9700 alignment); refresh-token **rotation** is the documented post-MVP path | [PRD D1](../requirements/product-requirements.md) |
| Abuse protection | Load shedding only | No per-client fairness — one hot client can consume shed capacity | Per-client rate limiting | Rate limiting needs client identity + distributed counters, out of step with MVP scope; shedding protects the service itself, which is the availability-critical half | [PRD D2](../requirements/product-requirements.md) |
| Delete semantics | Soft delete for articles + retention window + scheduled hard-purge; **PII hard-deleted** | `deleted_at` filter on every read query/index; purge worker to operate | Hard delete everywhere; soft delete everywhere | Recoverability and audit for content without unbounded table growth; PII must be truly erasable (GDPR/CCPA), so a flag is insufficient there | [PRD D3](../requirements/product-requirements.md) |
| Pagination | Keyset/cursor on `(created_at, id)` | No "jump to page N"; cursor opacity for clients | `OFFSET`/`LIMIT` | Constant cost at any page depth — `OFFSET` degrades linearly and would erode read P95 exactly where load is highest | [PRD D9](../requirements/product-requirements.md) |
| Update semantics | `PUT` full-replace | Clients must send the whole resource | `PATCH` partial update | Simplest, most predictable contract for MVP; `PATCH` adds merge semantics without an MVP need | [PRD D10](../requirements/product-requirements.md) |
| Write concurrency | Optimistic concurrency (version/`updated_at` precondition → `409`) | Clients must handle `409` retry | Pessimistic row locking | Lost-update protection at near-zero cost at MVP write volume (~5 write/s); no lock contention on the hot path | [PRD D11](../requirements/product-requirements.md) |
| List filtering | `author` filter only (+ implicit `deleted_at IS NULL`) | Less flexible querying for clients | Rich filtering / date ranges | Every filter is an index commitment on the read-heavy path; expand only with load-test evidence | [PRD D7](../requirements/product-requirements.md) |
| Orchestration | Kubernetes (committed by brief) | Operational complexity high for a single app | Plain Docker Compose / VM deploy | Buys rolling deploys, probes, and declarative horizontal scaling — the operability half of the NFRs; Compose remains the local dev path | [PRD §9 constraints](../requirements/product-requirements.md) |
| Development methodology | Outside-in TDD anchored on FR acceptance criteria | Slower first commit per feature; real test-maintenance burden | Test-after; coverage-only mandate | Tests that drove the design beat tests retrofitted to a coverage number; FR→test traceability comes free and the NFR ≥ 80% floor is met as a by-product | [ADR-0006](adr/0006-test-driven-development.md) |

## Narrative

The three headline non-functional targets are **read P95 < 200 ms**, **horizontal scaling**, and
**graceful degradation** ([NFR](../requirements/non-functional-requirements.md)). Each major trade-off
above buys one of them, and the costs were chosen to land where the workload is least sensitive.

**The latency target is bought with cache-aside plus a deliberately boring data model.** At a 100:1
read:write ratio, the only path that matters is the anonymous read. Cache-aside serves the target
≥ 90% of reads from Redis memory; keyset pagination (D9) keeps the miss path flat at any page depth;
and the author-only filter (D7) keeps the index footprint small enough that misses stay
index-resolved. The accepted cost — a bounded stale window after writes — lands on the least
sensitive axis: content freshness within a TTL, further tightened by write-time invalidation. The
[capacity model](capacity-scaling-model.md) quantifies the win: ~55 qps reaching MySQL instead of ~500.

**Horizontal scaling is bought with statelessness, and statelessness is bought with JWT.** The
monolith-vs-microservices debate matters less than it appears: what makes the API scale out is that
**no replica holds state** — not how many deployables there are (ADR-0002). Server-side sessions
would have re-introduced shared state on every authenticated request; stateless JWT (ADR-0005) removes
it, at the price of non-revocability — which D1 then bounds to a 15-minute exposure window rather than
solving with infrastructure (token denylists) the MVP doesn't need. The remaining shared resource is
the MySQL connection budget, which the capacity model bounds explicitly (~8 replicas at default
settings) with a documented next move.

**Graceful degradation is bought by keeping every optimization optional.** The system's failure
posture follows from one rule: **MySQL is the only component that must be correct; everything else
must be droppable.** Cache-aside (ADR-0004) makes Redis droppable — its loss is a latency event, never
a correctness event. Best-effort telemetry export keeps the observability stack droppable. Timeouts,
bounded retries, circuit breakers, and load shedding (chosen over per-client rate limiting, D2) make
MySQL impairment produce fast, explicit errors instead of cascading hangs. The accepted cost is that
degraded modes are *visibly* degraded — slower reads without Redis, `503 + Retry-After` under shedding —
which is precisely the intent: predictable partial service over unpredictable total failure.

**What was deliberately given up, and the escape hatches.** Independent deployability of auth vs.
content (split along the existing module seams if team or scale demands it); immediate token revocation
(add refresh-token rotation, then a denylist, post-MVP); per-client fairness (rate limiting when client
identity infrastructure exists); read-your-writes strictness inside the TTL window (shorten TTLs or
add explicit invalidation breadth if product feedback demands it); and "jump to page N" pagination
(not expected to return). Every deferral is recorded — in an ADR or a PRD decision — with its
re-entry condition, so growth is a sequence of planned moves rather than rewrites.
