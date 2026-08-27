# Bottleneck Analysis

> **Status:** ✅ Complete · **Owner:** Simon Sibomana · **Last updated:** 2026-08-27

What the load test found, ranked by measured cost. This document is the input to the optimization
work: a slow path that is not written down here does not get optimized.

The [load test plan](load-test-plan.md) states what the runs measure. The
[capacity scaling model](../architecture/capacity-scaling-model.md) lists the bottlenecks B1 to B6
that the design predicted. Each finding below names the B-number it confirms, or states that it is
new.

## Method

Findings come from the four-run matrix of 2026-08-22 — cache off, cache on, three replicas, and a
repeat of the cached single replica. Each run holds the same machine state: the `performance`
power profile, energy performance preference `performance`, RAPL PL1 45 W and PL2 115 W, AC power,
and the generator pinned to the eight E-cores. The matrix takes 68.5 minutes.

A second matrix, `2026-08-22-projection`, re-ran B and B2 after the list projection shipped. It
supplies every "after" figure in the resolutions. Both runs are citable, and their package
throttle deltas agree to 5.7%, inside the 10% limit.

Every number comes from one of two places: the k6 summary for the client view, or Prometheus over
the scenario window for the server view. Both appear where they disagree, because the disagreement
is itself a finding. Server-side latency is always filtered by `method` as well as `route`: the
route label alone blends a `GET` list with a `POST` create, which is the error corrected under F2.

### The noise floor

A finding needs a number, and the number must beat the spread between the two cached
single-replica runs. Run B and run B2 are the same workload 35 minutes apart:

| Scenario | p95 spread | p99 spread |
|---|---|---|
| `steady.js` | 0.23% | 0.49% |
| `ramp.js` | 0.54% | 8.3% |
| `spike.js` | 8.9% | 16.9% |

Read this table before you believe any comparison. A 5% improvement measured on `spike.js` is
drift. The same 5% on `steady.js` is real.

### What the runs produced

Client-side figures from the k6 summary. The ramp and spike rows describe behavior past the knee,
so treat them as a degradation shape, not as a latency measurement.

| Run | Cache | Replicas | `steady.js` p95 | `steady.js` p99 | `ramp.js` p95 | `spike.js` p95 |
|---|---|---|---|---|---|---|
| A | off | 1 | 14.451 ms | 23.155 ms | 2046.4 ms | 1778.4 ms |
| B | on | 1 | 14.642 ms | 21.133 ms | 346.8 ms | 913.1 ms |
| C | on | 3 | 14.742 ms | 19.310 ms | 15.5 ms | 16.2 ms |
| B2 | on | 1 | 14.675 ms | 21.237 ms | 348.7 ms | 994.6 ms |

At 200 rps every configuration returns the same p95 within 2%. The cache and the extra replicas
show up only in the tail, and only under the ramp and the spike. That is the first thing the
matrix says: **the scenario that carries the pass or fail line does not reach a bottleneck.**

## Findings

Ranked by measured cost.

### F1 — One app replica saturates at about 430 rps, and the cache does not raise that ceiling

**Evidence.** The ramp climbs in 30-second steps. Server-side request rate against server-side
p95, at the knee:

| Run | Last flat step | p95 there | First bent step | p95 there | Peak rps | p95 at peak |
|---|---|---|---|---|---|---|
| A — no cache, 1 replica | 391 rps | 21.6 ms | 429 rps | 60.3 ms | 449 | 87.8 ms |
| B — cache, 1 replica | 394 rps | 20.7 ms | 434 rps | 47.8 ms | 464 | 97.8 ms |
| C — cache, 3 replicas | 509 rps | 16.3 ms | none reached | — | 509 | 16.3 ms |

The cached and uncached knees are 429 rps and 434 rps. That gap is 1.2%, against a ramp p95 noise
floor of 0.54% and a rate step of about 35 rps — the two knees fall in the same step. Three
replicas carry 509 rps at 16.3 ms with no bend at all, and the ramp ends before run C degrades.

Two supporting numbers. Database query latency at the top of the ramp is 4.65 ms for run B and
1.00 ms for run C, for the same query count and the same MySQL instance. Both single-replica runs
collapse after the peak, from 449 to 410 rps and from 464 to 415 rps.

**Cause.** One uvicorn process runs one event loop. The per-request cost that saturates it is CPU
inside the application — object construction, validation, and serialization — not waiting on
MySQL or Redis. Run C spreads the identical work across three processes and the queueing
disappears, which is why the same database looks four times faster from run C.

**Cost.** A hard ceiling near 430 rps per replica. The cache is worth nothing to that ceiling.

**Maps to.** New. The model expected B2 (MySQL) or B3 (connections) to bite first. Neither did.

**Proposed fix.** Reduce per-request CPU, which F2 quantifies, and re-run the ramp to see whether
the knee moves. Scaling out already works and needs no change.

**Resolution.** ✅ Partly, and the size of the answer is the point. The list projection (F2) cut
per-request CPU and the ceiling moved 3%, from a mean peak of 467.5 rps to 481.9 rps. The pair
spread is 1.3% and 0.6%, so the gain is real and small.

| Measure | Baseline B / B2 | Projected B / B2 | Delta |
|---|---|---|---|
| Peak rate | 464.5 / 470.4 rps | 483.3 / 480.4 rps | **+3.1%** |
| Server p95 at the peak | 97.8 / 99.7 ms | 81.8 / 79.2 ms | **−18.5%** |
| Last flat ramp step | 394.4 / 405.4 rps | 410.3 / 403.6 rps | inside the spread |

Read that as the ceiling holding while the work under it got cheaper. Latency past the knee
improved everywhere — the ramp p95 fell 27% on the list read, 25% on the detail read and 27% on
update — but the rate at which the process runs out of headroom barely moved. One list read in
five was never the whole CPU budget. **A single replica saturates near 430 rps, and no payload
change alters that. Scale out.**

### F2 — The list endpoint returns full article bodies, which are 96.7% of the payload

**Evidence.** One default list page holds 20 items and 101,367 bytes. Article body text accounts
for 98,232 of those bytes, or **96.7% of the response**. The detail endpoint returns 5,086 bytes.

Server-side p95 by method and route, from `steady.js`, where the machine is quiet:


| Run | `GET /v1/articles` | `GET /v1/articles/{article_id}` | Ratio |
|---|---|---|---|
| B | 8.21 ms | 4.81 ms | 1.71x |
| B2 | 8.30 ms | 4.81 ms | 1.73x |

The list read costs 1.7 times the detail read while returning 20 times the rows, so per row it is
cheap. It is the bytes that are not. Database query p95 over the same windows is 0.97 ms, about
12% of the list read, and the rest is application time on work whose size scales with the body
text.

Redis pays the same tax. A cached list entry averages 23.5 KB against 4.7 KB for a cached article.

**Cause.** `ArticleListOut` carries the full `body` field, and the repository selects whole rows,
including the `MEDIUMTEXT` column. Nothing projects the column away for a list page.

**Cost.** A list read carries 34 times the bytes it needs. The serialization of those bytes is
application CPU, and application CPU is the resource that sets the ceiling in F1, so this is the
largest available lever on per-replica capacity.

**Maps to.** New. Item 2 of the plan's look-list, now with a number.

**Proposed fix.** Add an `ArticleSummaryOut` schema without `body`, and a repository projection so
MySQL stops reading `MEDIUMTEXT` for list queries. This breaks the API contract, which Simon
authorized while the project is pre-1.0. Expect the list page to fall from about 101 KB to about
3.4 KB.

**Resolution.** ✅ Done, measured by the `2026-08-22-projection` matrix. The page
prediction was accurate: 2,935 bytes against the 3.4 KB estimate.

| Measure | Baseline B / B2 | Projected B / B2 | Delta |
|---|---|---|---|
| Page of 20 items | 101,367 bytes | 2,935 bytes | **−97.1%** |
| Cached page in Redis | 23.5 KB | 3.67 KB | **−84%** |
| `GET /v1/articles` p95, steady | 8.21 / 8.30 ms | 5.58 / 5.47 ms | **−33.1%** |
| `GET /v1/articles/{id}` p95, steady | 4.81 / 4.81 ms | 4.80 / 4.80 ms | 0% (the control) |
| `GET /v1/articles` p95, ramp | 85.18 / 84.24 ms | 61.37 / 62.97 ms | **−26.6%** |
| `GET /v1/articles/{id}` p95, ramp | 44.25 / 44.06 ms | 32.64 / 33.47 ms | **−25.2%** |
| `PUT /v1/articles/{id}` p95, ramp | 125.98 / 124.37 ms | 90.47 / 91.03 ms | **−27.3%** |
| Client read p95, ramp | 329.6 / 336.4 ms | 65.5 / 71.5 ms | **−79.4%** |
| Client read p95, spike | 909.6 / 988.2 ms | 272.4 / 295.2 ms | **−70.1%** |
| Dropped iterations, ramp | 183 / 220 | 8 / 41 | **−88%** |
| Package throttle delta | 11,859 / 11,468 | 7,493 / 7,076 | **−37%** |

Three of those rows deserve a sentence each.

**The detail read improved too**, by 25% under the ramp, and nothing about it changed. That is
the F1 mechanism visible from the other side: one event loop serves every route, so cheaper list
reads leave more of it for everything else. Under `steady.js`, where the loop is not contended,
the detail read does not move at all — the perfect control for the whole comparison.

**The client saw a much larger win than the server did** — 79% against 27% on the same scenario.
The server histogram times the handler. The client figure also carries 101 KB across a socket,
parses it, and queues behind saturation. Both halves are real, and on a shared laptop the
generator's relief also hands CPU back to the service. Do not read the 79% as a server-side
number.

**Dropped iterations nearly vanished** (183 and 220, against 8 and 41). The generator was
spending its eight E-cores parsing 101 KB pages. That is a measurement-quality win as much as a
performance one.

What did not move: `db_queries_total` per request holds at 0.537, and the database query p95
holds at 0.96 ms. The projection was never about database time.

### F3 — The list cache serves under 10% of list reads

**Evidence.** Cache outcomes by entity, over each `steady.js` window:

| Run | `article` hits / misses | `article` ratio | `list` hits / misses | `list` ratio | Blended |
|---|---|---|---|---|---|
| B | 38,717 / 8,467 | 82.1% | 1,086 / 10,224 | 9.6% | 68.0% |
| C | 38,770 / 8,416 | 82.2% | 1,076 / 10,222 | 9.5% | 68.1% |
| B2 | 38,806 / 8,355 | 82.3% | 1,064 / 10,254 | 9.4% | 68.2% |

`cache_errors_total` is zero in every run. The ramp and the spike give the same picture, with list
ratios of 8.7% to 9.4%.

**Cause.** ADR-0010 invalidates list pages with a generation counter, so every write to any
article busts every unfiltered list page. The workload writes 5% of the time, which is often
enough to keep the counter moving faster than the pages are read.

**Cost.** Nine of every ten list reads fall through to MySQL. The blended ratio of 68% sits well
below the ≥ 90% target, and judging the blend alone would hide which half is failing.

**Maps to.** The documented accepted cost of ADR-0010. The decision recorded this trade-off before
anyone measured it; this is the measurement.

**Proposed fix.** Two options, and the choice is a decision rather than a tweak. Scope the
generation counter per author, so an unrelated write does not bust an author's page. Or accept
bounded staleness on unfiltered pages with a short absolute TTL. Take F2 first: a 3.4 KB list page
that misses is much cheaper than a 101 KB one.

**Resolution.** ⛔ Considered after F2, and declined. The hit ratio is unchanged at 9.6% and
9.4%, exactly as expected — the projection changes the size of a page, not who invalidates it.
What changed is the price of the miss, and the price is now too small to buy a staleness
trade-off with.

Cost the remaining misses. Over the 300-second steady window a replica takes 10,237 list misses,
which is 34 per second out of 210 requests per second. Each adds one database query at a p95 of
0.96 ms. A perfect list cache would therefore remove about **34 ms of database wait per second**,
and database wait is not the constrained resource: F1 shows the ceiling is application CPU, and
the whole database already runs at 0.537 queries per request with a flat 1 ms p95 from 200 rps to
509 rps.

Against that, both fixes cost something real. Per-author scoping is already implemented for
`author=N` pages (`app/cache/keys.py` keeps two counters); extending it to unfiltered pages is not
possible, because an unfiltered page contains every author by definition. A short absolute TTL
means serving a list that does not show an article the writer has already been told exists, and
`docs/data/caching-strategy.md` promises read-your-writes on this path.

**Decision: keep the generation counter as ADR-0010 specifies.** Revisit only if the write mix
rises well above 5%, or if a future profile shows list misses on the critical path. The 9.5% ratio
is not a defect; it is the measured price of a correctness guarantee, and F4 corrects the target
that made it look like one.

### F4 — The ≥ 90% hit ratio target cannot hold under the workload the plan specifies

**Evidence.** The article hit ratio measures 82.1%, 82.2% and 82.3% across three independent runs,
with zero cache errors and a correct picker. The workload sends 80% of reads to a hot set of 2,000
ids and 20% to the remaining 8,000. The cold 20% almost always misses, which caps the achievable
ratio near 80% plus whatever the cold tail repeats.

**Cause.** The target and the workload assumption disagree. A ≥ 90% ratio needs a skew tighter
than 80/20, or a TTL long enough to keep the cold tail resident.

**Cost.** None in latency. The cost is a document that would record a correct system as a failed
requirement.

**Maps to.** The cache hit ratio row of the capacity model validation table.

**Proposed fix.** State the target as a function of the access skew, and record 82% as the
measured value for an 80/20 hot set. Do not move the workload to reach the number.

**Resolution.** 🟨 Filled by the reporting work.

### F5 — Past the knee, requests are lost at connection level, not rejected by the application

**Evidence.** The application returned **zero non-2xx responses in every scenario of every run**.
The client disagrees:

| Run and scenario | k6 requests | Server requests | Lost | k6 failure rate |
|---|---|---|---|---|
| B `ramp.js` | 118,972 | 115,311 | 3,661 | 1.94% |
| B2 `ramp.js` | 118,961 | 117,592 | 1,369 | 1.82% |
| C `ramp.js` | 119,374 | 117,784 | 1,590 | 0.00% |
| B `steady.js` | 63,039 | 63,040 | −1 | 0.00% |

**Cause.** Past the knee the single replica stops accepting connections fast enough, and the
request never reaches an application handler. It is a queueing failure, not a rejection.

**Cost.** A failure rate of 1.8% to 1.9% on saturated single-replica ramps. Run C shows 0.00% at
the same offered rate, so the failure follows saturation, not the cache.

**Maps to.** A symptom of F1, recorded separately because it presents as an error rate.

**Proposed fix.** None on its own. Confirm it disappears when F1 moves. Check the nginx
`worker_connections` and listen backlog while you are there.

**Resolution.** 🟥 Still present, and the projection did not touch it. The ramp failure rate is
1.928% and 1.941% after the change, against 1.941% and 1.817% before — the same number. That is
the expected result: F1 barely moved, so the replica still saturates, and a saturated replica
still stops accepting connections. F5 closes when the offered rate stays under the knee, which is
a capacity decision, not a code change. Run three replicas and it is 0.00%.

### F6 — The connection pool fills only when the cache is off

**Evidence.** Peak pool state per run:

| Run and scenario | `in_use` | `overflow` |
|---|---|---|
| A `ramp.js` | 10 | 5 |
| A `spike.js` | 12 | 5 |
| B `ramp.js` | 6 | 0 |
| C, all scenarios | 1 | 0 |

Run A reaches the full 10 + 5 pool. Run B, the same hardware and the same replica count with the
cache on, peaks at 6 in use and never overflows.

**Cause.** Without the cache every read takes a connection. The measured 1.16 database queries per
request falls to 0.52 with the cache on.

**Cost.** Bounded. Run A still served 116,797 requests with zero 5xx responses, so the pool
throttled rather than failed.

**Maps to.** B3, partly. The model predicted a connection ceiling at eight or more replicas. It
appears instead on one replica with the cache off, and it does not appear at three replicas.

**Proposed fix.** None. The cache already removes it. Recorded so the next reader does not resize
the pool for the wrong reason.

**Resolution.** No change proposed.

## Findings ranked by cost

| Rank | Finding | Maps to | Cost | Fix | Resolution |
|---|---|---|---|---|---|
| 1 | F1 — one replica saturates near 430 rps, and the cache does not move that | new | Caps a replica at ~430 rps | Cut per-request CPU, then re-measure the knee | ✅ +3.1% peak; the ceiling holds. Scale out |
| 2 | F2 — list responses are 96.7% article body | new | A list read carries 34x the bytes it needs | Project `body` out of the list shape | ✅ −97% payload, −33% list p95 |
| 3 | F3 — list cache hit ratio is 9.5% | ADR-0010 accepted cost | 34 ms of database wait per second | Scope the generation counter, or accept bounded staleness | ⛔ Declined — costs less than the staleness it would buy |
| 4 | F4 — the ≥ 90% hit ratio target does not fit an 80/20 skew | capacity model | A correct system reads as a failed requirement | Restate the target against the skew | 🟨 Task 4 |
| 5 | F5 — 1.9% of requests are lost at connection level past the knee | symptom of F1 | 1.8% to 1.9% client failures on saturated ramps | Expect it to clear with F1 | 🟥 Unchanged — clears with replicas, not with code |
| 6 | F6 — the pool fills only with the cache off | B3, partly | None — throttles, does not fail | None | ✅ none needed |

## Ruled out

A ruled-out candidate is worth as much as a finding, because it stops the next reader looking
there again.

**MySQL is not the bottleneck (B2).** Query p95 is 0.96 ms to 0.97 ms at 200 rps in all four runs,
and 1.00 ms in run C at 509 rps. The database only slows down when the application queues in front
of it. The capacity model's "~500 qps uncached" figure survives contact: run A's knee of 429 rps
at 1.16 queries per request is 498 queries per second.

**Redis is not a bottleneck (B4).** `cache_errors_total` is zero across every run and every
scenario. Redis holds 2.05 MB for the whole working set.

**There is no hot-key stampede (B1).** The article hit ratio during the spike — 83.0%, 83.2% and
83.3% for B, C and B2 — matches the steady-state ratio. No miss burst appears on any spike window,
so the TTL jitter and single-flight mitigations hold.

**Password hashing does not appear in the tail.** The p95 for `/v1/auth/login` and
`/v1/auth/register` is undefined in every measured window, because `setup()` logs in once before
the window opens. Argon2 stays off the measured path, as `70aa3ee` intended.

**Thermal drift did not move the results.** A run and its repeat throttle within 3.3% of each
other. See the note below on what that replaced.

**The payload size assumption holds.** Mean article body is 4,912 bytes, inside the documented
2–8 KB range.

## Two corrections to the measurement method

Both are recorded here because they changed what the numbers mean.

**The absolute throttle cap tested the wrong hypothesis.** The citability rule failed a run whose
package throttle count rose by more than 1,000. All four runs failed it. The measured deltas —
16,015 for A, 11,859 for B, 2,123 for C, 11,468 for B2 — track how long each run spent saturated,
not how far the environment moved. Run A drove every read to MySQL and throttled most; run C never
reached its knee and throttled least. A cap of any value marks the run that finds the bottleneck
as the least trustworthy.

The replacement compares a run against its own repeat, where the workload is fixed and anything
left over is drift. B against B2 gives 3.3%, and `repeat_is_consistent` allows 10%. The threshold
was calibrated after the first matrix, from measured data, rather than derived beforehand. Nothing
in this document depends on the cap either way, because all four runs pass every environment check
that does hold: AC power, a stable power profile, and an unchanged generator cpuset.

**`steady.js` runs at 46% of the knee.** The plan calls for the steady scenario to sit at 80% of
the measured knee. It is configured at 200 rps, which was set before any knee was known, and the
single-replica knee is about 430 rps. The scenario that carries the pass or fail line therefore
never approaches a bottleneck, which is why all four runs return the same p95. The NFR result it
produces is real but weak. Re-point the rate near 350 rps once the optimization work settles, and
re-measure.

## What the optimization work changed

One change shipped. The plan named three steps, and the measurement ended the third.

1. **Project `body` out of list responses** (F2) — done, `37b0d38`. A list page fell from 101,367
   bytes to 2,935.
2. **Re-run the ramp** (F1) — done, matrix `2026-08-22-projection`, runs B and B2, both citable,
   the pair consistent to 5.7%. The knee moved 3%. Latency under it moved 25% to 33%.
3. **Revisit list invalidation** (F3) — declined on the evidence from step 2. The remaining cost
   of a list miss is about 34 ms of database wait per second, and the bottleneck is application
   CPU.

F4 belongs to the reporting work. F5 closes when the offered rate stays under the knee, which
means replicas. The connection pool was not resized.

### The one-sentence answer

The list projection bought a 97% smaller payload, a third off list read latency, a quarter off
every other route under load, and 3% more throughput — and it confirmed that per-replica capacity
on this service is set by the event loop, not by the read path, so the way to serve more traffic
is to run more replicas.

### What the next measurement should be

`steady.js` still runs at 200 rps, which is 42% of the measured peak. The scenario that carries
the NFR pass or fail line therefore never approaches a bottleneck, and every configuration returns
the same p95 there. Before the results go into `non-functional-requirements.md`, re-point the
steady rate near 80% of the measured knee — about 350 rps — and run it once more. That number is
the one worth writing down.
