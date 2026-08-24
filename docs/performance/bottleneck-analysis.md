# Bottleneck Analysis

> **Status:** 🟨 Awaiting the M6 measurement run · **Owner:** Simon Sibomana · **Last updated:** 2026-08-24

What the load test found, ranked by measured cost. This document is the input to the optimization
work: a slow path that is not written down here does not get optimized.

The [load test plan](load-test-plan.md) states what the runs measure. The
[capacity scaling model](../architecture/capacity-scaling-model.md) lists the bottlenecks B1 to B6
that the design predicted. Each finding below names the B-number it confirms, or states that it is
new.

## Method

Findings come from the four-run matrix — cache off, cache on, three replicas, and a repeat — with
Prometheus over each run window and the k6 summary beside it. A finding needs a number, and the
number must beat the noise floor: the spread between the two cached single-replica runs. A
difference smaller than that spread is drift.

## Findings

🟨 _Written by Task 2. Each finding takes the shape below._

### Template

**Evidence.** The metric, the query, and the number.

**Cause.** What in the code or the configuration produces it.

**Cost.** The latency or throughput it takes, at what offered rate.

**Maps to.** The B-number from the capacity scaling model, or "new".

**Proposed fix.** One change, and what it should move.

**Resolution.** 🟨 Filled by the optimization work: what changed, and the measured delta.

## Findings ranked by cost

| Rank | Finding | Maps to | Cost | Fix | Resolution |
|---|---|---|---|---|---|
| 🟨 | _pending_ | | | | |

## Ruled out

🟨 _Written by Task 2. Things the matrix checked and found not to be a limit. A ruled-out
candidate is worth as much as a finding, because it stops the next reader from looking there
again._
