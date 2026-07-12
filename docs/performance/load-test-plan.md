# Load Test Plan

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-12

## Objectives
- Validate **read P95 < 200ms** under target load.
- Find the scaling limit / saturation point.

## Tooling
_k6 or Locust (record choice). Scripts stored under `tests/load/` or `perf/`._

## Workload Profiles
| Scenario | Mix | Pattern | Target |
|---|---|---|---|
| Steady read | 95% read / 5% write | constant VUs | sustained throughput |
| Ramp | read-heavy | step ramp | find knee point |
| Spike | read-heavy | sudden burst | degradation behavior |

## Environment
- _Hardware/replicas, DB/Redis sizing, dataset size — must be stated for results to be meaningful._

## Metrics Captured
- P50/P95/P99 latency, throughput (req/s), error rate, cache hit ratio, CPU/mem.

## Method
1. Seed representative dataset.
2. Run baseline (no cache / single replica).
3. Run optimized (cache + tuned pool + scaled replicas).
4. Record results in [load-test-report.md](load-test-report.md).
