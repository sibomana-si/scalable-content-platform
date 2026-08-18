# ADR-0010: Generation counters for list-page invalidation

> **Status:** Accepted · **Date:** 2026-08-17

## Context

[ADR-0004](0004-redis-cache-aside.md) chose cache-aside and said a write must "invalidate the
affected keys". For article detail that is one line of work: the key is `article:{id}`, the write
knows the id, and it deletes one key.

List pages are the hard part. A page key encodes the page size, the keyset cursor, and the
optional author filter, so the key space is unbounded and is populated lazily by whatever pages
readers request. A write knows which **row** changed. It does not know which cached **pages**
contain that row.

Four constraints rule out the naive answers:

1. **No `SCAN` or `KEYS` on the write path.** `KEYS` blocks the server. `SCAN` costs a variable
   number of round trips that grows with the key space. Neither belongs in a request.
2. **Invalidation must be fail-open.** A Redis error after a successful commit must not turn a 201
   into a 500.
3. **Bounded staleness only.** A stale read inside one TTL is an accepted cost. A stale read that
   outlives its TTL, or that no later write can clear, is a correctness defect.
4. **Keyset pagination anchors pages.** Each page is defined by an explicit `(created_at, id)`
   cursor, not by an offset, so removing a row does not shift the window of any other page.

## Decision

Every list page key embeds a **generation number**, and a write advances the counter with one
`INCR`. Every key built from the old value becomes unreachable at once. Orphans expire on their
own TTL.

Two counters, not one:

```text
articles:list:gen                 -> unfiltered pages
articles:list:gen:author:{id}     -> author=N pages
```

A write to article 42 by author 5 runs:

```text
DEL  article:42
INCR articles:list:gen
INCR articles:list:gen:author:5
```

Author 9's cached pages survive. The full key table is in
[caching-strategy](../../data/caching-strategy.md).

### Why the second counter earns its `INCR`

At the documented peak of about five writes per second across about 1,000 registered users:

```text
global generation:      ~5 bumps/s        -> a cached unfiltered page lives ~200 ms
per-author generation:  ~5/1000 bumps/s   -> a cached author page lives ~200 s
list TTL:               60 s
```

An unfiltered page dies about 300 times faster than its TTL. A typical author-filtered page
outlives its TTL and gets full value from the cache. One extra Redis command converts the
author-filtered key space from useless to useful.

## Alternatives considered

**A single global counter.** One `INCR` per write instead of two, and simpler. Rejected because a
write to one author's article drops every other author's cached pages, which makes the entire
author-filtered key space worthless under any sustained write rate.

**A per-key registry.** Record page membership at population time — `SADD article:42:listkeys
<page key>` for each row on the page — then delete exactly the affected pages on a write. It gives
the smallest blast radius and the highest list hit ratio. Rejected on three counts:

- **Write amplification on the read path.** Caching one page of 20 rows costs 20 `SADD` commands.
  The read path is the performance-critical path, and this puts work on it to save work on the
  rarer write path. That is the wrong trade at a 100:1 read/write ratio.
- **Unbounded auxiliary state.** Each registry set grows with every page a row appears on and needs
  its own expiry and trimming. Redis eviction can drop a registry set while the pages it tracks
  survive, which silently converts precise invalidation into no invalidation.
- **A create has no row to look up.** A new article appears in no registry set, so a create must
  bust the first page by other means. The registry does not cover the one write type that always
  changes a page.

**Caching article detail only.** No invalidation problem exists, and detail carries the hit-ratio
target. Rejected because the incremental cost of option C is two `INCR` commands and a key-naming
rule, and the arithmetic above shows author-filtered pages do benefit.

## Consequences

**Positive**

- O(1) invalidation per write, whatever the cache holds. No `SCAN`, no `KEYS`, no registry.
- A stale page cannot be served. An unreachable key is not a wrong key.
- No auxiliary state to trim, expire, or leak.
- A lost counter fails safe. If Redis loses it, the counter restarts at 1 and old pages become
  unreachable rather than wrongly reachable. The cost is a cold cache, never a stale read.

**Negative**

- **Unfiltered list pages have a low hit ratio under sustained writes.** This is inherent to
  generation counters, not a defect. Article detail carries the ≥ 90% target. If M6 load testing
  shows list reads dominate and this hurts, the levers are a longer list TTL or a soft-TTL
  refresh, not a switch to a registry.
- Orphaned keys hold memory until TTL expiry, bounded by the number of distinct pages cached
  within one 60-second window. The `allkeys-lru` policy is the safety net.
- One extra Redis command per write compared to a single global counter.

## Validation

| Claim | Test |
|---|---|
| A write emits the right `DEL` and `INCR` set | `tests/unit/test_cache_invalidation.py` |
| A write by author A leaves author B's generation untouched | `tests/unit/test_article_cache.py`, `tests/integration/test_article_cache_redis.py` |
| A bump makes the previous page unreachable | `tests/integration/test_article_cache_redis.py` |
| Invalidation runs after the commit, never before | `tests/integration/test_invalidation_ordering.py` |
| A Redis failure during invalidation does not fail the write | `tests/unit/test_cache_invalidation.py` |
| A read after a write never returns the pre-write body | `tests/acceptance/test_fr004_cache.py` |

Measure the list hit ratio separately from the detail hit ratio at M6. The `entity` label on
`cache_hits_total` exists for that comparison, and it is the number that decides whether list
caching keeps its place.
