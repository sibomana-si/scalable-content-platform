# Caching Strategy

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-12

Redis cache-aside for hot reads, with invalidation on writes.

## Pattern
**Cache-aside (lazy loading):** read checks cache → on miss, load from MySQL and populate cache with TTL. See diagram in [architecture/diagrams](../architecture/diagrams/README.md).

## Key Design
| Entity | Key pattern | TTL | Invalidated on |
|---|---|---|---|
| Article by id | `article:{id}` | _e.g. 300s_ | update/delete of that id |
| Article list page | `articles:list:{filterhash}:{page}` | _e.g. 60s_ | any article create/update/delete |

## Invalidation Rules
- Writes invalidate (or update) affected keys within the same request path.
- Prefer explicit delete-on-write over relying solely on TTL for correctness-sensitive data.

## Failure & Degradation
- **Cache unavailable:** fall back to DB (degraded latency, not an outage). See [resilience](../resilience/fault-tolerance-design.md).
- **Thundering herd / stampede:** _mitigation — locks, request coalescing, or jittered TTLs._
- **Stale reads:** acceptable window documented per entity above.
