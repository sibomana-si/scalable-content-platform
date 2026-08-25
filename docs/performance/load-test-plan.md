# Load Test Plan

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-08-25

What M6 measures, on what workload, and under what conditions a number counts.

This document holds the intent. The [load test runbook](load-test-runbook.md) holds the commands.
The [load test report](load-test-report.md) holds the results. `tests/load/lib/slo.json` holds the
numbers in machine-readable form, and `tests/unit/test_load_profile.py` fails the build when
`slo.json` and the [non-functional requirements](../requirements/non-functional-requirements.md)
disagree.

## Objectives

1. Validate the five performance rows of the NFR table: read P50, read P95, read P99, write P95,
   and sustained throughput.
2. Find the knee point — the arrival rate at which latency leaves the target band.
3. Measure the value of the cache, as a difference between a cached and an uncached run.
4. Prove that throughput grows with replica count, or find the shared-state ceiling that stops it.
5. Produce a numbered bottleneck list that Task 3 can act on.

## What this test can prove, and what it cannot

One laptop runs the system under test and the load generator. Absolute throughput from that setup
is indicative, not a production figure, and the report says so in plain words.

The defensible results are the comparisons, because both sides carry the same error:

- cache off against cache on, at one replica — the value of the cache;
- one replica against three — whether throughput scales;
- before against after each optimization — whether the change did anything.

A repeat of the cached single-replica run gives the noise floor. A difference smaller than the
spread between those two runs is drift, not a result.

## Tooling
**k6 0.57.0**, run as a container behind the `load` compose profile. Recorded in
[ADR-0011](../architecture/adr/0011-k6-for-load-testing.md).

The reasons, in the order they mattered:

| Reason | Detail |
|---|---|
| Generator cost is measurement error | The generator shares a laptop with MySQL, Redis, three replicas, Prometheus, and Grafana. A Go generator on eight E-cores leaves all six P-cores to the service. |
| Thresholds set the exit code | The NFR table becomes a declarative `thresholds` block. A missed target fails the run, with no reading of a summary by eye. |
| One time axis | The Prometheus remote-write output puts client-side latency beside `cache_hits_total` and `db_pool_connections` in the same Grafana window. |
| No dependency cost | A container adds nothing to `requirements.lock`, `pytest` collection, or the coverage floor. |

The accepted cost: JavaScript enters a Python repository, and `ruff` does not lint it.
`tests/load/selftest.js` and `tests/unit/test_load_profile.py` cover that gap.

## Workload profiles

Three scenarios, all open-model. Each is a k6 arrival-rate executor, never a virtual-user count.

A closed model self-limits. When the server slows, the generator sends less, so throughput
flattens into a line that hides saturation instead of showing it. An open model holds the offered
rate and lets the queue grow, which is what a knee point looks like.

| Scenario | Script | Executor | Shape | Answers |
|---|---|---|---|---|
| Steady | `steady.js` | `constant-arrival-rate` | 200 rps for 5 minutes | Does the service hold the NFR targets at the target rate? |
| Ramp | `ramp.js` | `ramping-arrival-rate` | 50 rps to 500 rps, 10 steps of 45 seconds | Where is the knee? |
| Spike | `spike.js` | `ramping-arrival-rate` | 50 rps, jump to 500 for 30 seconds, back to 50 | How far does it degrade, and how fast does it recover? |

`steady.js` carries the SLO thresholds and sets the exit code. `ramp.js` carries none on purpose:
it climbs past the rate at which the targets hold, so a threshold there would fail by design.

Every scenario runs the same request mix: 95% reads and 5% writes. Reads split 80/20 between
article detail and list pages. Writes are a create, and an update that does a read, then a
conditional `PUT` with `If-Match`. That read-modify-write pair is realistic and exercises cache
invalidation.

Every request is tagged `op:read_detail`, `op:read_list`, `op:write_create`, or `op:write_update`,
with a `Trend` behind each tag, so the write P95 target is measured separately from the read one.

## The hot-set assumption

The [capacity scaling model](../architecture/capacity-scaling-model.md) assumes hot-set dominated
access, and the ≥ 90% cache hit ratio target holds only under that skew.

The read scenarios draw ids from a skewed distribution: 20% of the articles take 80% of the reads.
Both numbers live in `slo.json` as `hot_set_share` and `hot_set_traffic`, and the report prints
them beside the measured hit ratio.

Get this wrong and every downstream number is fiction. A uniform draw across 10,000 rows against a
300-second TTL measures a cache that cannot work, and the run would "prove" the design fails when
the workload was wrong. A hot set of five rows measures a cache that cannot fail. So the picker
has its own test: `tests/load/selftest.js` asserts the distribution over 100,000 draws, and runs
in about a second with no infrastructure.

## Dataset

`scripts/seed_load_dataset.py` writes it through the SQLAlchemy models, in batches, before the run.

| Property | Value | Why |
|---|---|---|
| Articles | 10,000 | Matches the capacity model. Large enough that the working set does not fit trivially in Redis. |
| Authors | 50 | Gives the author filter something to select on. |
| Author skew | 80/20 | The same shape as the read skew, so filtered lists are not uniformly cheap. |
| Body size | 2 KB to 8 KB | The capacity model's assumption. The list endpoint returns full bodies today, so this drives payload size. |
| Seed | 1337 | The dataset is reproducible. Two matrices meet the same rows. |

The seeder is idempotent: a second run tops the count up rather than duplicating. Load-test
accounts use the reserved `loadtest.example` domain and an unusable password hash, so none of them
can be logged into.

Do not seed through the API. 10,000 `POST` requests are slow, pollute the metrics you are about to
read, and give no control over the author distribution.

## Environment

| Element | Value |
|---|---|
| Host | One laptop, Intel i7-12700H, 6 P-cores and 8 E-cores, Ubuntu |
| System under test | MySQL 8, Redis 7, one to three app replicas behind nginx, all in compose |
| Generator | k6 0.57.0 in a container, pinned to the E-cores |
| Observability | Prometheus and Grafana in compose, scraping each replica directly |
| Power | AC, profile locked to `performance` for the whole matrix |

The exact machine state of each run is captured as JSON before and after it, and committed beside
the results. `scripts/run_metadata.py` validates the schema and decides whether the pair is
citable.

## Measurement hygiene

Five rules. Each removes a way for the numbers to be wrong while looking fine.

**Pin the generator to the E-cores.** The k6 service sets `cpuset: "12-19"`, so the generator
takes the eight E-cores and every P-core stays with the service. This is the largest single
reduction in the shared-host error, and it costs one compose line. Watch `dropped_iterations`: if
the generator cannot hold the offered rate on E-cores alone, widen the set and say so in the
report.

**Hold one power policy across every run.** Use `powerprofilesctl set performance`, not
`cpupower frequency-set`. `power-profiles-daemon` is active and reverts a raw `sysfs` write
mid-run, which is the worst failure mode available, because the run still produces numbers. The
gain is variance, not speed. A pinned floor reaches the 45 W PL1 clamp sooner, not later. The
point is that every run meets the same machine, so the comparisons hold.

**Treat thermal drift as data.** Record `package_throttle_count` and `core_throttle_count` before
and after every run. A run whose delta moves materially is flagged and repeated. Keep a fixed
60-second settle between runs so each starts from the same thermal state.

**Leave the machine alone while a run is in progress.** Start the matrix and touch nothing until
it ends. The generator holds eight E-cores and the service holds six P-cores, so a shell command,
an editor, or a test suite takes CPU from one of them. In the first matrix a `python3` read of a
results file overlapped run B, and that run dropped 301 iterations where the same scenario under
run A dropped none. The cost of the rule is patience. The cost of breaking it is a number you
cannot explain.

**Discard the warm-up.** Each run starts with 30 seconds at 50 rps whose results are thrown away.
It fills the connection pools and the cache, so the measured run does not average a cold start
into a steady state.

## Metrics captured

From k6, per operation tag: P50, P95, P99, and maximum latency; request rate; error rate;
`dropped_iterations`.

From Prometheus, over the run window: `http_request_duration_seconds` by route,
`db_query_duration_seconds`, statements per request, `cache_hits_total` and `cache_misses_total`
by entity, `cache_errors_total`, and `db_pool_connections` by state.

Judge `entity="article"` and `entity="list"` separately. ADR-0010 busts every unfiltered list page
on every write, so one blended ratio hides the cost that decision accepted.

## Method

1. Seed the dataset to 10,000 articles.
2. Run A: cache off, one replica. The uncached ceiling.
3. Run B: cache on, one replica. Per-replica capacity and the hit ratio.
4. Run C: cache on, three replicas. Whether throughput scales.
5. Run B2: a repeat of B. The noise floor.
6. Record the findings in [bottleneck-analysis.md](bottleneck-analysis.md), ranked by measured
   cost.
7. Optimize what the analysis named, one change at a time, and re-run the affected scenario.
8. Record the results in [load-test-report.md](load-test-report.md).

Each run executes `ramp.js`, then `steady.js`, then `spike.js`, between two machine-state
snapshots. The [runbook](load-test-runbook.md) carries the commands.

## Exit criteria

A run counts when all six hold:

1. Prometheus scrapes every replica for the whole window.
2. k6 reports zero dropped iterations.
3. The k6 request count agrees with the Prometheus request rate over the same window.
4. The machine-state pair passes `run_metadata.is_citable`.
5. The power profile reads `performance` after the last run.
6. The cache hit ratio during run B sits in a band the report states and defends.

A missed target is reported as a miss, with the cause and the next move. A ✅ obtained by moving
the line is worth less than a 🟥 with an explanation.
