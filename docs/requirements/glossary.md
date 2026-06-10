# Glossary / Ubiquitous Language

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-06-10

Single source of truth for domain terms. Keep code, API, and docs aligned with these definitions.

## Domain terms
| Term | Definition |
|---|---|
| **Article** | The core content entity (CRUD target). Owned by an Author; publicly readable unless soft-deleted. Body stored raw (Markdown convention), rendered client-side. |
| **User** | An authenticated principal with an account and a role. |
| **Role** | A named set of permissions. The system defines two: `user` and `admin`. |
| **Author** | A registered User who created an Article. Authors may modify/delete their own Articles; authorship is immutable (no transfer in MVP). |
| **Administrator** (`admin`) | A User with elevated privileges: may manage user roles and modify/delete any Article. |
| **Anonymous Reader** | An unauthenticated client that consumes public read endpoints (`GET` article / list). Not an API role; the highest-volume actor. |
| **PII** | Personally Identifiable Information (e.g., a User's email). Subject to data minimization and the right to erasure — hard-deleted/anonymized, never merely soft-deleted. |

## Auth & security
| Term | Definition |
|---|---|
| **JWT** | JSON Web Token — a signed, self-contained token. Used here as the stateless **Access token**. |
| **Access token** | Short-lived JWT proving authentication (15-minute TTL in MVP; no refresh token — clients re-authenticate on expiry). |
| **RBAC** | Role-Based Access Control — authorization by Role (`user`/`admin`), enforced via middleware on write/admin endpoints. |

## Data & caching
| Term | Definition |
|---|---|
| **Cache-aside** | Read pattern: check cache → on miss, read DB and populate cache. Writes invalidate affected entries. |
| **Cache stampede** | A burst of concurrent DB loads when a hot key expires/misses simultaneously. Mitigated by TTL jitter and single-flight locking. |
| **Soft delete** | Marking a record (e.g., an Article) as deleted (`deleted_at`) so it is excluded from reads, rather than physically removing the row. |
| **Keyset/cursor pagination** | Paging by a stable cursor (e.g., `(created_at, id)`) instead of `OFFSET`; constant cost regardless of page depth. The MVP pagination strategy ([PRD §13 D9](product-requirements.md)). |
| **Idempotency** | A request that can be retried with the same effect as applying it once. Background jobs (e.g., purge) are designed to be idempotent. |
| **Expand/contract migration** | Zero-downtime schema change: add the new shape (expand), migrate/deploy, then remove the old (contract). |
| **PITR** | Point-In-Time Recovery — restoring the database to an arbitrary moment using backups + binlog. Bounds the **RPO**. |

## Reliability & operations
| Term | Definition |
|---|---|
| **SLI** | Service Level Indicator — a measured signal (e.g., % of reads < 200 ms). |
| **SLO** | Service Level Objective — a target for an SLI over a window (e.g., 99.9% availability / 30 days). |
| **Error budget** | `1 − SLO` — the allowed amount of failure in a window. Its exhaustion triggers a change freeze. |
| **RPO** | Recovery Point Objective — maximum acceptable data loss, measured in time (target ≤ 5 min). |
| **RTO** | Recovery Time Objective — maximum acceptable restore time (target ≤ 1 hour). |
| **RED method** | Metrics discipline: **R**ate, **E**rrors, **D**uration per endpoint — the core request-path signals. |
| **Load shedding** | Deliberately rejecting/short-circuiting excess load to protect the service when saturated. |
| **Circuit breaker** | A resilience pattern that stops calling a failing dependency for a cool-off period, failing fast instead of cascading. |
| **`request_id`** | A unique id assigned per request and propagated across logs, metrics, and traces for correlation. |
| **DORA** | DevOps Research & Assessment — source of the four delivery metrics (deploy frequency, lead time, change-failure rate, recovery time). |
