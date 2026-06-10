# ADR-0004: Redis cache-aside for hot reads

> **Status:** Accepted · **Date:** 2026-06-10

## Context
The read path is the performance-critical path: anonymous article reads dominate ~100:1 and must hold
P95 < 200 ms at ~500 req/s ([NFR](../../requirements/non-functional-requirements.md)). Serving every
read from MySQL couples read latency and DB load to readership growth. A cache is required; the
question is the **pattern** — who owns population, and what happens when the cache is wrong or gone.
Graceful degradation is a first-class requirement: Redis must be allowed to be cold or absent at any
time ([PRD §9](../../requirements/product-requirements.md)).

## Decision
We will use **cache-aside (lazy loading) in application code**: on read, try Redis; on miss, read
MySQL, then populate Redis with a **TTL + jitter**; on write, persist to MySQL and **invalidate** the
affected keys (delete, not update). Stampede control via single-flight locking on hot-key misses.
MySQL remains the source of truth (ADR-0003). Full key/TTL/invalidation design in
[caching-strategy](../../data/caching-strategy.md).

## Consequences
**Positive**
- The cache is an optimization, never a dependency: a cold/absent Redis means slower reads, never wrong data — exactly the degradation posture the NFR requires.
- Only actually-read data occupies memory (lazy population suits a hot-set-skewed workload; [capacity model](../capacity-scaling-model.md) shows even the full corpus fits easily).
- At a ≥ 90% hit ratio, MySQL sees ~55 qps instead of ~500 — the headroom behind the P95 target.

**Negative / costs accepted**
- **Bounded staleness:** a read may serve a value up to one TTL old if invalidation misses an edge; mitigated by write-time invalidation + modest TTLs.
- First read of any key pays the miss penalty (DB read + cache set).
- Stampede risk on hot-key expiry is real and must be engineered away (TTL jitter, single-flight) rather than ignored.
- Invalidation logic lives in application code and must be tested (the classic cache-aside failure mode is a forgotten invalidation on a new write path).

**Neutral / follow-ups**
- List endpoints cache bounded cursor pages; write invalidation covers affected list keys (detail in caching-strategy).
- If M6 shows hit ratio below target, the levers are TTL tuning and key design — not a pattern change.

## Alternatives Considered
| Option | Pros | Cons | Why rejected |
|---|---|---|---|
| Write-through | Cache always warm after writes; no first-read miss | Cache becomes a write-path dependency; cold-start and cache-loss still need the aside path anyway; writes pay double | Couples write availability to Redis — inverts the required degradation posture |
| Read-through (cache library owns loading) | Centralized load logic | Less control over per-key TTL/jitter/single-flight; another abstraction on the hottest path | Cache-aside gives the same effect with explicit, instrumentable control |
| No cache (MySQL + indexes only) | Simplest; no staleness | DB load scales linearly with readership; P95 target at 500 req/s rests entirely on one DB | Forfeits the 9× load reduction; fails the design intent of a shielded read path |
