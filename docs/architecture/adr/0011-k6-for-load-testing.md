# ADR-0011: k6 for load testing

> **Status:** Accepted · **Date:** 2026-08-24

## Context

M6 must put numbers against the five performance rows of the
[non-functional requirements](../../requirements/non-functional-requirements.md) and against the
seven validation rows of the [capacity scaling model](../capacity-scaling-model.md). None of those
rows carries a measurement today.

The measurement runs on one laptop. That laptop runs MySQL, Redis, up to three app replicas,
nginx, Prometheus, Grafana, and the load generator at the same time. Every CPU cycle the
generator takes is a cycle the service does not get, so generator cost is not a preference. It is
a term in the measurement error.

Two tools were considered: k6 and Locust.

Locust is the closer fit on paper. It is Python, so it would share the repository's language, the
existing virtual environment, and the developers' habits. Its scenarios could import
`app.schemas` directly.

## Decision

Use **k6 0.57.0**, run as a container behind a `load` compose profile. Load scripts live in
`tests/load/` as JavaScript. No load-testing dependency enters `requirements.txt`.

Four reasons, in the order they decided it.

**1. Generator cost.** k6 is a Go binary that drives thousands of concurrent requests from a
handful of OS threads. Locust drives them from Python greenlets and needs multiple worker
processes to reach the same rate. On a shared host, that difference lands directly in the results.
The k6 service is pinned with `cpuset: "12-19"`, so the generator takes the eight E-cores and all
six P-cores stay with the service under test. A Python generator would have needed more of them.

**2. Thresholds set the exit code.** k6 `thresholds` express the NFR table declaratively:

```javascript
thresholds: {
  'http_req_duration{op:read_detail}': ['p(95)<200', 'p(99)<450'],
  'http_req_failed': ['rate<0.001'],
  'dropped_iterations': ['count==0'],
}
```

A missed target fails the run. Nobody reads a summary by eye and decides. Locust has no equivalent
and needs custom event hooks to do the same job.

**3. One time axis.** The k6 Prometheus remote-write output puts client-side latency into the same
Prometheus that already holds `cache_hits_total`, `db_query_duration_seconds`, and
`db_pool_connections`. A Grafana window then shows offered rate, observed latency, and cache
behavior together. Correlating three sources by hand costs more than it sounds like it does.

**4. Dependency isolation.** As a container, k6 adds nothing to `requirements.lock`, nothing to
`pytest` collection, and nothing to the 80% coverage floor. A Locust dependency would join the
production resolution set and would need a `pip-audit` pass on every bump.

## Consequences

**Positive**

- The generator is cheap enough to run beside the system under test, and pinnable away from it.
- The NFR targets exist once, in `tests/load/lib/slo.json`, and
  `tests/unit/test_load_profile.py` fails when they drift from the requirements document.
- Client-side and server-side metrics share a time axis with no extra work.
- The load tooling upgrades by changing one image tag.

**Negative**

- **JavaScript enters a Python repository, and `ruff` does not lint it.** This is the real cost.
  Two things cover the gap: `tests/load/selftest.js` asserts the library's behavior with k6's own
  `check`, and `tests/unit/test_load_profile.py` asserts that every scenario file exists, is named
  in the [load test plan](../../performance/load-test-plan.md), and matches the NFR numbers.
- Load scripts cannot import the application's Pydantic schemas, so a request body shape is
  duplicated in `lib/api.js`. An acceptance test would catch a drift, because the scenario would
  start returning 422.
- k6 has no seedable random source. `lib/workload.js` carries a small mulberry32 generator, so the
  hot-set draw is reproducible between runs.
- Running k6 needs Docker. A contributor without it can still run `pytest`, because the load tests
  sit behind a compose profile and outside `testpaths`.

## Alternatives

| Option | Why not |
|---|---|
| Locust | Python and familiar, but costs more host CPU for the same rate, has no declarative thresholds, needs a custom exporter for Prometheus, and enters the production dependency set. |
| `pytest` plus `asyncio` | No arrival-rate executor, no percentile reporting, and the generator would share the event loop with nothing to stop it drifting off the offered rate. |
| Apache Bench or `hey` | One URL and no session. Cannot express a 95/5 mix, a cursor walk, or a conditional `PUT`. |
| A hosted service | Cannot reach a service on `localhost`, and the point of M6 is the local stack. |

## Validation

| Claim | Test |
|---|---|
| `slo.json` matches the NFR table | `tests/unit/test_load_profile.py` |
| Every scenario file exists and is named in the plan | `tests/unit/test_load_profile.py` |
| The hot-set picker follows the documented share | `tests/load/selftest.js` |
| The cursor walk terminates | `tests/load/selftest.js` |
| Prometheus scrapes each replica, and not through nginx | `tests/unit/test_dashboards.py` |
| The runbook's commands resolve to real files, profiles, and variables | `tests/unit/test_load_runbook.py` |
