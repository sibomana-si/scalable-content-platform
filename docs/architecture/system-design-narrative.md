# System design narrative

> **Status:** 🟩 Complete · **Owner:** Simon Sibomana · **Last updated:** 2026-09-10

This page tells the design story in one read, from the requirement to the measurement. The
[overview](overview.md) is the structure and the [trade-off analysis](trade-off-analysis.md) is
the decision table. This page is the argument: what the system had to do, what bets the design
placed, what the build found when it tested those bets, and what the numbers say now. Every figure
links the document it comes from.

## The problem

The [product requirements](../requirements/product-requirements.md) ask for a read-heavy content
API behind a CMS or a SaaS content surface. Anonymous clients read articles at high volume. A
small number of authenticated authors write them. The planning ratio is **100:1** reads to
writes, and the peak is about **500 req/s**.

Three targets shaped every choice. Reads must hold a **P95 < 200 ms** under the target load
([PRD goal G1](../requirements/product-requirements.md)). The service must stay up **99.9%** of
the time and degrade rather than fail when a dependency is impaired
([NFR](../requirements/non-functional-requirements.md)). Every write and admin route must enforce
JWT and role-based access.

One constraint shaped the build itself. The project had eight weeks and one laptop, which ran the
replicas, the databases, the observability stack and the load generator together.

## The bets

**Stateless replicas behind a load balancer.** No replica holds a session or an in-process cache,
so any replica can answer any request and scale-out is a number in a compose file. The cost is
that every piece of state must live in MySQL or Redis, which puts those two systems on every
path. [ADR-0002](adr/0002-modular-monolith.md) records the shape: one deployable with the auth
and content modules kept behind service interfaces, so the seams stay clean split lines if a team
ever needs them.

**MySQL as the one source of truth.** All writes go to MySQL in a transaction, and all invariants
live there: ownership, roles, soft-delete state. Redis may be cold, stale within a TTL, or absent
without any effect on correctness. The cost is a single-writer ceiling, which the capacity model
accepts at about five writes per second, and no transactional DDL, which keeps migrations small
and reversible. [ADR-0003](adr/0003-mysql-source-of-truth.md) records the choice.

**Cache-aside on the hot set.** A read tries Redis first, reads MySQL on a miss, and writes the
value back under a jittered TTL. A write invalidates the affected keys and never updates the
cache in place. The costs are bounded staleness, a miss penalty on the first read of every key,
and a stampede risk on hot-key expiry, which TTL jitter and a single-flight lock engineer away.
[ADR-0004](adr/0004-redis-cache-aside.md) records the decision and the
[read path diagram](diagrams/read-path.mmd) shows the sequence.

**JWT without a session store.** A token carries the user id and the role claim, and any replica
validates it by signature alone. The cost is no immediate revocation: a stolen token lives until
it expires, and the 15-minute TTL bounds that exposure. Role changes take effect at the next
issuance. [ADR-0005](adr/0005-stateless-jwt-auth.md) records the trade, and the
[auth flow diagram](diagrams/auth-flow.mmd) shows the middleware order, with 401 before 403.

**A modular monolith with the seams named.** One image, one pipeline, one deployment to operate.
Auth and content are separate packages that talk through service interfaces, never through each
other's tables. The cost is a shared failure domain for code defects and a discipline to keep the
boundary from eroding. The [overview](overview.md) §3 shows where each seam sits in the code.

## What building it found

The bets held. What the build found were the places where the design was right in outline and
wrong in a detail, and each detail came from a measurement or a fault.

**The invalidation window.** The first cache shipped with no invalidation, so a TTL was the only
bound on staleness. The direct fix, an invalidation inside the handler, runs before the
transaction commits. A concurrent reader could then miss, read the pre-commit row, and repopulate
the cache with the old value for a full TTL, with no error and no metric. The fix queues each
invalidation on the session and drains the queue after the commit, and a rollback discards it.
The [caching strategy](../data/caching-strategy.md) explains the ordering.

**Generation counters for list pages.** A write can touch any list page that holds the article,
and the page keys depend on the cursor and the author filter, so no writer can enumerate them.
Deleting by pattern needs a `SCAN`, and a registry of page keys is a second cache to keep
correct. The design embeds a generation counter in every list key, so one `INCR` retires every
page at once, and a second counter per author keeps other authors' pages addressable.
[ADR-0010](adr/0010-generation-counter-list-invalidation.md) records the arithmetic.

**The list payload nobody had measured.** The bottleneck analysis found that a default list page
of 20 items carried **101,367 bytes**, and that the article body was **96.7% of the response**.
A projection that drops the body took the page to **2,935 bytes**. The change moved latency, not
the throughput ceiling, which the
[bottleneck analysis](../performance/bottleneck-analysis.md) F2 records as a finding in its own
right.

**The knee and the noise floor.** One replica bends near **450 req/s** and three bend near
**900 req/s**, so throughput grows with replicas but not in proportion to them. A run and its
repeat 40 minutes apart differed by **4.4% on read P95**, so the report states that floor with
every result: a change under about 7% is drift, not a result. The
[load-test report](../performance/load-test-report.md) carries both numbers and the method
behind them.

**The hit ratio the plan assumed.** The capacity model planned for a 90% hit ratio. The runs
measured **89.4%** on articles and **73.5%** blended, because the list cache serves
**under 10% of list reads**: every write retires every list page, and the write rate in the
profile is high enough to keep them cold. The
[bottleneck analysis](../performance/bottleneck-analysis.md) F3 and F4 record the cause, and the
[capacity model](capacity-scaling-model.md) now carries the measured values and says what the
ratio is a function of.

**Little's law turns a knee into a shed limit.** Past the knee, the load test lost requests at
the connection level, where no server-side counter could see them. The resilience work turned the
measured knee into a ceiling: **450 req/s** at a P95 of 0.2 s gives **90 requests in flight**,
and a middleware refuses the 91st with a 503 and a `Retry-After`.
[ADR-0012](adr/0012-timeout-retry-and-circuit-breaker-policy.md) records the arithmetic, and
the [failure handling diagram](diagrams/failure-handling.mmd) shows where the guard sits.

**The refused connection that answered 500.** The design said a refused MySQL connection answers
503 and opens the breaker. The first run of chaos experiment 4 showed 500s and a breaker that
never opened, because the retry's rollback closed the request transaction and the second attempt
ran on a dead session. The fix made the commit and the rollback explicit, so the session begins
again after a rollback. The re-test held **zero 500s**. The
[chaos report](../resilience/chaos-test-report.md) keeps the failed run in its evidence table,
marked, because it is the run that found the defect.

## What the numbers say

Three replicas on one host held **525 req/s** for five minutes at a read P50 of **5.7 ms**, a
read P95 of **10.9 ms**, a read P99 of **22.3 ms**, and a write P95 of **26.2 ms**, with zero
errors. Every row of the performance table in the
[non-functional requirements](../requirements/non-functional-requirements.md) carries a measured
value that passes its target.

Five chaos experiments and one repeat ran under the same 5-minute steady load. A dead Redis cost
**0.7 ms** of P95 and zero failures, because every read fell through to MySQL and readiness
stayed degraded rather than failed. A dead MySQL still served half the reads from cache, refused
the rest with a 503 in under 5 ms, and recovered to zero 5xx inside **65 s** on the breaker's own
probe. No experiment produced a 500 after the fix. The
[chaos report](../resilience/chaos-test-report.md) holds the per-experiment table, and the
[observability flow diagram](diagrams/observability-flow.mmd) shows how each signal reached the
dashboards that the report reads from.

Two caveats attach to every number. The host ran the replicas, the databases and the generator
together, so absolute rates are indicative and the scaling curve mixes service behavior with
contention on the laptop. And the noise floor is **4.4% on read P95**, so the
[load-test report](../performance/load-test-report.md) treats any smaller change as drift.

## What is next

The gaps are stated rather than hidden. The Kubernetes section of the
[deployment guide](../operations/deployment.md) names no manifests, so the orchestration story
is a claim until a cluster runs it. The OpenAPI examples, the threat model, the OWASP matrix and
the operations runbook are marked in progress in the [documentation index](../README.md). There
is no real deployment target, so the availability SLO is a design posture, not a measured
uptime. And the **900 req/s** ceiling belongs to one laptop; a multi-host run is the way to
separate sub-linear scaling in the service from contention on the host, and that run has not
happened ([load-test report](../performance/load-test-report.md)).

## How to read the rest

Open these three next, in this order:

1. [Architecture overview](overview.md) — the container and component views, and §6 on what the
   measurements changed.
2. [Load-test report](../performance/load-test-report.md) — the method, the matrix, and the
   noise floor.
3. [Chaos test report](../resilience/chaos-test-report.md) — five faults, what each cost, and
   the defect they found.
