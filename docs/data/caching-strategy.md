# Caching Strategy

> **Status:** ✅ Implemented · **Owner:** Simon Sibomana · **Last updated:** 2026-08-17

Redis cache-aside for hot reads, with invalidation on writes. The cache is an optimization and
never a dependency: every path degrades to MySQL. See
[ADR-0004](../architecture/adr/0004-redis-cache-aside.md) for the decision and
[ADR-0010](../architecture/adr/0010-generation-counter-list-invalidation.md) for how list pages
are invalidated.

## Pattern
**Cache-aside (lazy loading):** a read checks the cache, and on a miss it loads from MySQL and
populates the cache under a TTL. See the [read path diagram](../architecture/diagrams/README.md).

The write path never reads the cache. `ArticleService.get` is cache-aside; `ArticleService._load`
always reads MySQL, and `update`, `delete`, and the ownership check use only `_load`. A stale
cached `author_id` would decide ownership on stale data, and a stale `updated_at` would break the
compare-and-set precondition.

## Key design
| Entity | Key pattern | TTL | Invalidated on |
|---|---|---|---|
| Article by id | `article:{id}` | 300s ± 20% | update or delete of that id |
| Unfiltered list page | `articles:list:g{gen}:all:{filterhash}` | 60s ± 20% | any article create, update, or delete |
| Author list page | `articles:list:a{author}:g{authgen}:{filterhash}` | 60s ± 20% | a create, update, or delete by that author |
| Global list generation | `articles:list:gen` | none | never expires; only advances |
| Author list generation | `articles:list:gen:author:{author}` | none | never expires; only advances |
| Single-flight lock | `lock:{key}` | `CACHE_LOCK_TIMEOUT_SECONDS` | released by the loader |

`filterhash` is a 16-character SHA-256 digest of the page size and the decoded keyset cursor, so
two different page windows never share a key.

The builders live in `app/cache/keys.py`, are pure, and are unit-tested in
`tests/unit/test_cache_keys.py`.

### TTL jitter

Every TTL is spread over `base ± base × CACHE_TTL_JITTER`. A fixed TTL expires a whole generation
of keys in the same instant, and every reader then misses together. The spread turns a mass expiry
into a trickle.

## Invalidation rules
A list page key embeds a **generation number**. A write advances the counter with one `INCR`, so
every key built from the old value becomes unreachable. There is no `SCAN` on the write path and
no key registry to keep in step. Orphaned pages expire on their own TTL.

| Write | Effect |
|---|---|
| Create an article by author A | `INCR articles:list:gen`; `INCR articles:list:gen:author:A` |
| Update article `id` by author A | `DEL article:{id}`; both `INCR` commands |
| Delete article `id` by author A | `DEL article:{id}`; both `INCR` commands |

Two counters, not one. A write by author A leaves author B's cached pages addressable. The cost
is one extra `INCR` per write; the benefit is that author-filtered pages live long enough to be
worth caching. See [ADR-0010](../architecture/adr/0010-generation-counter-list-invalidation.md)
for the arithmetic and the rejected alternatives.

### Ordering

**Invalidation runs after the transaction commits, never inside the handler.** `get_session`
commits in its teardown, so an inline invalidation would run before the row is durable. A
concurrent reader could then miss, read the pre-commit row, and repopulate the cache with the old
value — which would survive its full TTL, with no error and no metric.

## Failure and degradation

Every method of `ArticleCache` catches `RedisError` and `TimeoutError`, counts the failure in
`cache_errors_total`, logs it once, and degrades: a read returns `None` and the caller loads from
MySQL, and a write does nothing. Nothing in the module raises into a request. The bounded
`REDIS_SOCKET_TIMEOUT` is what makes the promise keepable — an unbounded command has no failure
to catch.

- **Cache unavailable:** reads fall back to MySQL. Latency degrades; the endpoint does not fail.
  See [fault tolerance](../resilience/fault-tolerance-design.md).
- **One failure latches the rest of the request.** A cache-aside read makes four Redis calls —
  get, lock, set, release. A blackholed server answers none of them, so without a latch each call
  pays the full socket timeout and a 2-second timeout becomes an 8-second request. The read still
  succeeds, which makes it worse: nothing fails and everything is slow. One failure is enough
  evidence to skip the rest. The latch lives for one request; the next request probes Redis
  again. A cross-request circuit breaker is M7 work.
- **Thundering herd:** single-flight. On a miss the reader claims a `SET NX` lock; the winner
  loads from MySQL and populates the key. A loser polls the key for up to
  `CACHE_LOCK_TIMEOUT_SECONDS` and then reads MySQL itself. A loser never waits on the lock
  indefinitely — a slow loader must cost a duplicate query, never a hung request. This is
  bottleneck **B1** in the [capacity model](../architecture/capacity-scaling-model.md).
- **Corrupt or outdated entry:** an unparsable body counts as a miss. It is reloaded from MySQL
  and overwritten, so a schema change cannot turn cached data into a 500.
- **Stale reads:** bounded by the TTL in the key table. A write clears the affected keys, so the
  window is the invalidation gap, not the full TTL.

## Disabling the cache

Set `CACHE_ENABLED=false`. The read path then issues no Redis command at all — no connection, no
keys — and every read goes to MySQL. Use it to exercise the fall-through path without stopping the
container, and to isolate the cache when a latency regression could be on either side.

A disabled cache records no hit and no miss. Counting misses would make a deliberate
configuration read as a fault on the dashboard.

## Measurement

| Metric | Labels | Reads as |
|---|---|---|
| `cache_hits_total` | `entity` (`article`, `list`) | Reads served from Redis |
| `cache_misses_total` | `entity` | Reads that fell through to MySQL |
| `cache_errors_total` | `operation` (`get`, `set`, `delete`, `incr`, `lock`) | Degraded operations |

The `entity` label exists so the list hit ratio can be judged separately from the detail hit
ratio. Article detail carries the ≥ 90% target; unfiltered list pages bust on every write by
design. The [Cache dashboard](../observability/dashboards.md) plots both.

