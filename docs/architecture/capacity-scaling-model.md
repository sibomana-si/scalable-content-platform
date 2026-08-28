# Capacity & Scaling Model

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-08-28

Back-of-the-envelope sizing that justifies the scaling claims. These are **planning numbers, not
commitments** — each is validated (or corrected) by the M6 load test, and this document is updated
with measured values, mirroring the Target/Measured convention in the
[NFR](../requirements/non-functional-requirements.md).

## Workload Assumptions

| Parameter | Assumption | Source |
|---|---|---|
| Registered users | ~1,000 | [PRD §9 capacity assumptions](../requirements/product-requirements.md) |
| Articles stored | ~10,000 | PRD §9 |
| Read:write ratio | ≈ 100:1 | PRD §9 (read-heavy by design) |
| Peak load | ≈ 500 req/s sustained | PRD §9 · [NFR throughput target](../requirements/non-functional-requirements.md) |
| → of which reads / writes | ≈ 495 read/s · ≈ 5 write/s | derived from the 100:1 ratio |
| Avg article payload | ~2–8 KB JSON (bounded text body, no media) | PRD §9 assumption "bounded text"; refine at M6 |
| Cache hit ratio (target) | ≥ 90% on hot reads | [NFR scalability](../requirements/non-functional-requirements.md) |
| Access skew | Hot-set dominated (popular articles take most reads) | typical content workload; verify at M6 |

## Derived Sizing

### MySQL load — the quantified case for the cache

At target hit ratio, the database sees only cache misses plus writes:

```
DB read qps  = 495 read/s × (1 − 0.90 hit)  ≈ 50 qps
DB write qps =                                ≈ 5 qps
DB total     ≈ 55 qps   (vs ≈ 500 qps uncached — a ~9× reduction)
```

~55 qps of indexed primary-key/keyset lookups is comfortable for a single modest MySQL instance.
The corollary matters for resilience planning: **if Redis disappears, MySQL absorbs ~500 qps** — the
fall-through case must be load-tested at M6, and load shedding exists for exactly this scenario
([fault-tolerance-design](../resilience/fault-tolerance-design.md)).

### Connection pooling

**Status:** implemented at M5. The pool settings below are the shipped defaults in
`app/config.py`, and ``tests/unit/test_capacity_model.py`` runs this arithmetic against them,
so the code and this document cannot drift apart.

Total connections must stay within MySQL's limit as replicas scale
([NFR](../requirements/non-functional-requirements.md)):

```
total_connections = replicas × (pool_size + max_overflow) + worker + admin_headroom
max replicas      = (max_connections − reserved) ÷ per-replica max
```

Worked example with MySQL's default `max_connections = 151`, reserving ~20 for the purge worker,
migrations, and operator sessions:

| Setting | Value |
|---|---|
| Per-replica pool (`pool_size` / `max_overflow`) | 10 / 5 → **15 max** |
| App replicas at MVP | 4 → 4 × 15 = **60 connections** |
| Headroom check | 60 + 20 reserved = 80 ≪ 151 ✅ |
| Replica ceiling at these settings | (151 − 20) ÷ 15 ≈ **8 replicas** |

Beyond ~8 replicas, either shrink per-replica pools, raise `max_connections` (with memory care), or
front MySQL with a proxy/pooler (ProxySQL) — see bottleneck B3 below. Redis connections are far
lighter (default `maxclients` 10,000) and do not constrain replica count at this scale, but the
pool is capped at `REDIS_MAX_CONNECTIONS` (default 50) anyway: redis-py grows its pool without
limit, so a stalled server would open a socket for every waiting caller.

Every bound makes a failure bounded.**`DB_POOL_TIMEOUT` (10s) is the one that matters most: an
unbounded pool wait turns one slow query into a total stall, because every later request queues
behind it and nothing ever fails, so nothing ever alerts. A request that cannot get a connection
must raise inside the timeout — `tests/integration/test_pool_exhaustion.py` proves it does, and
proves that returning a connection unblocks a waiter. `DB_POOL_RECYCLE` (1800s) sits under both
MySQL's `wait_timeout` and a typical proxy idle timeout, so a connection is replaced before the
other end drops it.

**Watching it.** `db_pool_connections{state="in_use"|"available"|"overflow"}` is sampled at scrape
time rather than per request, so it costs nothing between scrapes. Sustained `overflow` above zero
means `pool_size` is undersized for the traffic; `in_use` pinned at `pool_size + max_overflow` with
requests failing means the ceiling has been reached, and the next step is B3 below. The panels are
on the [Database dashboard(../observability/dashboards.md)].

### Redis memory

```
article cache  ≈ 10,000 articles × 8 KB (upper avg) × ~1.5 overhead ≈ 120 MB worst case
list pages     ≈ bounded set of hot cursor pages × page size        ≈ a few MB
```

Even caching **every** article costs ~120 MB — the realistic hot set is far smaller. A **256 MB**
Redis allocation is generous at MVP scale. Policy: TTL is the primary eviction mechanism (with
jitter, per [caching-strategy](../data/caching-strategy.md)); `maxmemory-policy allkeys-lru` as the
safety net — correct for a pure cache where MySQL is the source of truth and any key is rebuildable.

### App replicas

```
replicas = ceil( peak RPS ÷ per-replica capacity ) + 1 headroom
```

Per-replica capacity is **measured, not assumed**. M6 measured it: one replica bends at about
**450 req/s** ([load-test-report](../performance/load-test-report.md)). The arithmetic follows:

```
replicas = ceil( 500 req/s ÷ 450 req/s ) + 1 headroom = 3
```

Three replicas is the MVP sizing, and M6 ran it: 525 req/s sustained for five minutes at a read
P95 of 10.9 ms with zero errors.

Two corrections to the planning prose this paragraph replaced. The old estimate of "several
hundred RPS per replica" was right, and the resulting 2–4 replica range was right, but for the
wrong reason — the limit is **CPU inside one uvicorn event loop**, not the database and not the
cache (finding F1). And scaling is **sub-linear on this host**: three replicas bend near 900 req/s,
about twice one replica rather than three times. Size with the measured figure, not with a linear
extrapolation, until a multi-host run separates the service cost from contention on the machine.

## Scaling Limits & Bottlenecks

Where this design stops scaling, in the order limits are expected to bite, and the documented next move:

| # | Bottleneck | Symptom | Next move |
|---|---|---|---|
| B1 | **Hot-key stampede** on expiry of a popular article | DB load spike, read P95 breach | Already mitigated: TTL jitter + single-flight ([caching-strategy](../data/caching-strategy.md)); then longer TTLs / soft-TTL refresh |
| B2 | **Single MySQL instance** saturates (reads first — misses + fall-through) | Rising DB latency, replication of load spikes into P95 | **Read replicas** behind the repository layer; writes stay on the primary (clean fit — reads dominate 100:1) |
| B3 | **Connection ceiling** as replicas multiply | Pool exhaustion / connection errors at ~8+ replicas | Shrink pools, then a pooling proxy (ProxySQL); revisit `max_connections` |
| B4 | **Single Redis node** (memory or throughput) | Evictions of hot keys, cache latency | Larger node first; Redis Cluster only if the hot set outgrows one node (unlikely at this scale) |
| B5 | **Single MySQL writer** (writes scale vertically only) | Write latency under sustained write growth | Out of MVP scope; documented path: vertical scale → functional partitioning → sharding |
| B6 | **Single region** | Latency for distant readers; regional blast radius | Out of scope ([PRD §9](../requirements/product-requirements.md)); path: CDN/edge cache for public reads before multi-region anything |

### What the M6 matrix found

The four-run matrix of 2026-08-22 measured this list for the first time. Full evidence is in the
[bottleneck analysis](../performance/bottleneck-analysis.md); this table records which limits
appeared, and at what load.

| # | Appeared? | At what load | Evidence |
|---|---|---|---|
| B1 | **No** | Not at 464 rps | The article hit ratio during the spike (83.0–83.3%) matches steady state. No miss burst on any spike window, so TTL jitter and single-flight hold |
| B2 | **No** | Not at 509 rps | Query P95 stays at 0.96–1.00 ms from 200 rps to 509 rps. The "~500 qps uncached" figure validated: run A's knee of 429 rps at 1.16 queries per request is 498 qps |
| B3 | **Partly** | 1 replica, cache off, ~430 rps | The pool filled (`in_use` 10 + `overflow` 5) only in run A. With the cache on it peaked at 6 in use and never overflowed. The model predicted this at 8+ replicas; it appears instead without a cache |
| B4 | **No** | Not at 509 rps | `cache_errors_total` is zero in every run. Redis holds 2.05 MB for the whole working set |
| B5 | **Not exercised** | — | The 5% write mix never approached a write ceiling |
| B6 | **Not exercised** | — | Single-host test by design |

The matrix found a limit the list does not name, and it bites before any of them: **one app
replica saturates near 430 rps, and the cache does not raise that ceiling** (finding F1). The cost
is CPU inside the application — object construction, validation, and serialization — not the
database and not the cache. Add it to the list as B0, the limit that arrives first.

B0 was then tested directly. Removing article bodies from list responses cut the payload by 97%
and list read latency by a third, and the peak rate moved from a mean of 467.5 rps to 481.9 rps —
**3%**. The ceiling holds while the work under it gets cheaper, which settles the scaling
question: on this service you buy throughput with replicas, not with payload. The replica
arithmetic at the top of this page is therefore the right instrument, and per-replica capacity is
the number to keep measuring.

The deliberate MVP position: **scale the stateless tier horizontally, shield the stateful tier with
the cache, and document — rather than build — the next rung of each ladder** until measurements
justify it ([trade-off-analysis](trade-off-analysis.md)).

## Validation

Each assumption above is validated at M6 and the measured value recorded here
([load-test-plan](../performance/load-test-plan.md) → [load-test-report](../performance/load-test-report.md)):

| Claim to validate | Measured | Status |
|---|---|---|
| Per-replica capacity (RPS at P95 < 200 ms) | **≈ 450 req/s** — the knee sits at 444–475 req/s (run B) and 429–464 req/s (run B2), at a P95 of 20–24 ms | ✅ |
| Cache hit ratio ≥ 90% under the target profile | **89.4%** on articles, **73.5%** blended, under an 80/20 hot set | 🟨 |
| MySQL load ≈ 55 qps at target hit ratio | **248 qps** normalized to 500 req/s (0.496 queries per request), against 55 predicted | 🟥 |
| Fall-through survival: MySQL at ~500 qps with Redis disabled | _pending fault-injection test (M7)_ | 🟥 |
| Correctness under scale-out (no shared-state ceiling) | `tests/integration/test_horizontal_scaling.py`, `tests/integration/test_multi_replica.py` | 🟩 |
| Throughput scales with added replicas | **Yes, sub-linearly** — 450 req/s on one replica, 900 req/s on three | 🟨 |
| Avg payload size 2–8 KB and Redis memory model | **4,912 bytes** mean body, inside the range; Redis holds **2.05 MB** for the whole working set against 120 MB modeled worst case | ✅ |

Three of these need a sentence, because the number alone misleads.

**The MySQL estimate was wrong, and the cause is the list cache.** The model divided by a 90%
blended hit ratio. The measured blended ratio is 72.3%, because list pages hit under 10% of the
time — generation-counter invalidation busts every unfiltered page on every write (ADR-0010,
finding F3). At 0.496 queries per request the corrected form is:

```
DB qps = offered rps × queries per request
       = 500 × 0.496 ≈ 248 qps
```

MySQL absorbed it without strain — query P95 held at 1.9 ms throughout — so the design conclusion
stands even though the arithmetic did not. Use 0.5 queries per request as the planning figure and
re-measure it whenever the invalidation strategy changes.

**The hit ratio target is a function of the access skew.** An 80/20 hot set caps the achievable
article ratio near 80% plus the repeats in the cold tail, and 89.4% is what that workload can
give. The target is not missed by the system; it was stated without the skew it depends on
(finding F4).

**Scaling is sub-linear on one host.** Three replicas returned twice the capacity, not three
times, on a laptop that also ran MySQL, Redis, nginx, Prometheus, Grafana, and the generator. The
next measurement is a run with the generator on a separate machine.
