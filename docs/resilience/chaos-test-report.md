# Chaos / Failure-Injection Report
### Validates [fault-tolerance-design.md](fault-tolerance-design.md).

> **Status:** 🟩 Complete — five experiments plus a repeat, run against the resilient build · **Owner:** Simon Sibomana · **Last updated:** 2026-09-02

## Method
Faults are injected at the network with [Toxiproxy](https://github.com/Shopify/toxiproxy), which
sits between the application and each dependency under the `chaos` compose profile. The procedure
is [chaos-test-runbook.md](chaos-test-runbook.md); the injector is `scripts/inject_fault.py`.

A proxy is the right instrument here because stopping a container cannot express "slow", and
stopping MySQL loses the data: that service declares no named volume. Four faults cover the four
failure modes the design claims to survive.

| Fault | What the dependency does | Toxiproxy toxic |
|---|---|---|
| `latency` | Answers, late | `latency`, downstream |
| `timeout` | Answers nothing, then closes after the given wait | `timeout` with the wait in milliseconds |
| `blackhole` | Holds the connection open and never answers | `timeout` with a wait of 0 |
| `reset_peer` | Refuses at once | `reset_peer` with a wait of 0 |

Every experiment is a five-minute steady k6 run at 350 req/s against a host uvicorn through the
proxy. The fault is injected 60 seconds in and cleared 180 seconds later, so one run holds three
phases — before, under fault, and recovery — and the report reads each phase from Prometheus over
its own window. The client-side view comes from the k6 summary, which spans all three.

Three rules govern every number that reaches this report.

1. **The machine is held still.** Each run is bracketed by `scripts/perf_env.sh report`, and the
   pair must pass `scripts/run_metadata.py` before a figure is cited. A chaos snapshot carries one
   key the load snapshots do not, `toxiproxy_cpuset`, because the injector competes for the same
   silicon as the service.
2. **The baseline runs through the proxy.** Run 0 is the steady scenario with no toxics injected.
   Every delta is measured against it, never against the M6 figures, so the proxy hop lands in the
   baseline rather than in the result.
3. **The toxic value comes from the timeout budget.** A latency smaller than the configured
   statement timeout trips nothing and measures the proxy. Each experiment records the value it
   injected and the bound it was sized against.

## Baseline: what the system does today

Measured on 2026-08-29 against commit `b226364`, before any resilience code existed. One request
at a time against a host uvicorn through the proxy, with 200 seeded articles and a warm Redis.
These are single-request probes, not a load run: they establish what breaks, not how much.

| Probe | No fault | MySQL `latency 3000` | MySQL `blackhole` | MySQL `reset_peer` | Redis `blackhole` |
|---|---|---|---|---|---|
| `GET /v1/articles/{id}`, cache miss | 200 in 14 ms | **200 in 12.03 s** | **500 in 10.05 s** | **500 in 12 ms** | 200 in 2.01 s |
| `GET /health/ready` | 200 `ready` | 503 in 2.00 s | 503 in 2.01 s | 503 in 4 ms | **503 in 2.01 s** |

Five findings, in the order they cost the most.

**B1 — a slow database has no bound at all.** A 3 s delay on the MySQL socket turned a 14 ms read
into a 12.03 s read, and the request still returned 200. The delay is paid on every round trip the
driver makes, so the cost is a multiple of the injected value and not the value itself. Nothing in
`app/db/session.py` sets a connect timeout or a statement timeout, so the only ceiling on a read
is the client giving up.

**B2 — an unreachable database costs 10 s and then a 500.** The blackhole probe waited 10.05 s and
returned `INTERNAL_ERROR`. That 10 s is `db_pool_timeout`, which is a queueing bound rather than a
call bound: it is the accidental ceiling, not a designed one. The response is the catch-all handler
in `app/api/errors.py`, so a caller sees a server bug rather than a dependency outage, with no
`Retry-After` and no way to tell the two apart.

**B3 — a refused connection is fast and still wrong.** `reset_peer` failed in 12 ms, which is the
right speed and the wrong answer: another `INTERNAL_ERROR`. There is no breaker, so every request
pays the same failed connection attempt for as long as the outage lasts.

**B4 — a dead Redis costs 2 s per request, forever.** `ArticleCache._degrade` latches, so one
request pays one socket timeout rather than several. The latch is per request, so the next request
pays it again. Reads stay correct — the fall-through to MySQL works, which is the property
ADR-0004 promises — but every read costs `redis_socket_timeout` on top, with no cross-request
memory of the outage.

**B5 — readiness fails on a latency event.** Redis down returned 503 from `/health/ready` while
MySQL was healthy and the service was serving correct data. In a load balancer that pulls a working
replica out of rotation for a fault ADR-0004 defines as a latency event, never a correctness one.

## Experiments

Run against the resilient build, with the steady k6 scenario underneath, so the behavior is
measured and not asserted. Experiment 3 is read as the breaker exercise against a blackholed
MySQL: the modular monolith has no third dependency for the template's "downstream" row.

| # | Injected failure | Hypothesis | Observed behavior | Pass? |
|---|---|---|---|---|
| 0 | None, through the proxy | The proxy hop is small and stable | 367.6 req/s held for 5 minutes at P95 18.0 ms and P99 24.7 ms, zero 5xx, zero dropped iterations, 8 requests in flight at the peak | 🟩 |
| 1 | MySQL `latency 2500`, sized between the 2.0 s statement timeout and the 3.0 s call timeout | Timeouts trip inside the bound, retries stay bounded | Server P95 **fell** to 4.8 ms. 31,300 reads still answered 200 from cache; 24,746 answered 503 `breaker_open` and 13 answered 504 `upstream_timeout`. The breaker opened 13 times and no request returned 500 | 🟩 |
| 2 | Redis unavailable (`blackhole`) | Fall through to MySQL, elevated latency, the breaker opens once | **Zero failures.** 367.7 req/s held, P95 moved 19.4 → 20.1 ms. Cache hits went to 0 and MySQL took 430 qps against 188 cached, at a query P95 of 1.54 ms. The Redis breaker opened about 14 times and readiness stayed 200 `degraded` | 🟩 |
| 3 | MySQL blackholed | The breaker opens, 503 returns fast, no queue builds | 27,930 reads served from cache, 27,140 refused with 503 `breaker_open` at P95 4.8 ms. Load shedding fired for the first time — 823 responses — with the in-flight peak at 85 against the ceiling of 90. Recovery to zero 5xx inside 65 s | 🟩 |
| 4 | MySQL unavailable (`reset_peer`) | Cached reads served, writes rejected cleanly, no 500s | **After the fix in Finding F1:** 37,759 reads served from cache, 18,240 refused with 503 `breaker_open` at P95 4.8 ms, **zero 500s**, the breaker opened 16 times, and the in-flight peak was 3. Throughput held at 350 req/s with no dropped iterations | 🟩 |

### The noise floor

Experiment 1 was run twice, as `chaos-1` and `chaos-1r`, because no reader can tell a real change
from drift without a repeat. The two runs agree on everything the design claims and disagree on
one number.

| Figure | `chaos-1` | `chaos-1r` |
|---|---|---|
| Throughput under fault | 350.4 req/s | 350.0 req/s |
| Server P95 under fault | 4.80 ms | 4.85 ms |
| 5xx share under fault | 44.2% | 74.0% |
| 500 responses | 0 | 0 |
| Recovery to zero 5xx | 65 s | 64 s |

**Treat the degraded share as a shape, not a figure.** The latency experiment stands where the
breaker cycles: MySQL answers late enough to trip the call timeout and fast enough to pass the
half-open probe, so how much traffic the cache absorbs between two cycles decides the split
between 200 and 503. A 30-point spread across two identical runs is the honest noise floor for
that column, and no claim in this report rests on it. Latency, throughput, the absence of 500s
and the recovery time repeat to within 2%.

## Evidence

Under `docs/resilience/results/`, one k6 summary and a bracketing pair of machine-state snapshots
per run:

| Run | Fault | Machine state | Citable |
|---|---|---|---|
| 0 | None | `chaos-0-before.json`, `chaos-0-after.json` | Yes |
| 1 | MySQL `latency 2500` | `chaos-1-before.json`, `chaos-1-after.json` | Yes |
| 1r | MySQL `latency 2500`, repeat | `chaos-1r-before.json`, `chaos-1r-after.json` | Yes |
| 2 | Redis `blackhole` | `chaos-2-before.json`, `chaos-2-after.json` | Yes |
| 3 | MySQL `blackhole` | `chaos-3-before.json`, `chaos-3-after.json` | Yes |
| 4 | MySQL `reset_peer`, before the F1 fix | `chaos-4-before.json`, `chaos-4-after.json` | No — the package throttle count rose by 23,804 during the run |
| 4c | MySQL `reset_peer`, after the F1 fix | `chaos-4c-before.json`, `chaos-4c-after.json` | Yes |

The k6 summaries are `steady-chaos-0.json` through `steady-chaos-4c.json` in the same directory.
Run 4 is kept and marked rather than deleted: it is the run that found F1, and the defect it found
does not depend on the machine holding still. Every number quoted from it is a count of 500
responses or an order-of-magnitude collapse, not a latency measurement.

Server-side figures come from Prometheus over each phase window; client-side figures come from the
k6 summary, which spans all three phases of a run.

## Findings & Improvements

**F1 — a refused connection answered 500, and the breaker never opened.** Experiment 4 was the only
experiment to fail. Under `reset_peer` the service returned **12,166 × 500**, throughput collapsed
from 368 to 101 req/s, P95 rose to 954 ms and P99 to 4.2 s, k6 escalated to 600 VUs and dropped
43,526 iterations, and `circuit_breaker_state{dependency="mysql"}` never left 0. Experiment 3 had
already proved the same dependency, blackholed, answering 503 correctly, so the fault was not "MySQL
is gone" but "MySQL refuses fast".

The speed was the whole difference. A blackholed host expires the 3-second call timeout before a
second attempt exists; a refused connection fails in milliseconds and reaches one. `with_retry`
rolls the session back between attempts, the request transaction lived inside an
`async with session.begin()` block, and a rollback closes that block. Every statement after it
raised `InvalidRequestError` — an error the retry layer does not call transient. Nothing translated
it, so `guarded_call` read it as an answer from a working dependency, recorded a breaker
**success**, and the catch-all handler in `app/api/errors.py` answered 500. The retry meant to make
the read survive was what broke it.

The fix makes the commit and the rollback in `app/db/session.py` explicit instead of a context
manager, so the session begins again after a rollback and the second attempt reaches MySQL. Only
reads retry, so no write crosses two transactions.
`tests/integration/test_retry_after_rollback.py` pins both halves: the session serves a statement
after a rollback, and a connection cut mid-read raises `DependencyUnavailableError` and counts
against the breaker.

| Under the fault | Before (`chaos-4`) | After (`chaos-4c`) |
|---|---|---|
| 500 responses | 12,166 | **0** |
| MySQL breaker opens | 0 | 16 |
| Throughput | 101 req/s | 350 req/s |
| Server P95 | 954 ms | 4.8 ms |
| Server P99 | 4,228 ms | 5.0 ms |
| Requests in flight, peak | 52 | 3 |
| Shed responses | 1,086 | 0 |
| k6 dropped iterations | 43,526 | 0 |
| k6 peak VUs | 600 | 100 |

**F2 — load shedding fired, once, and the ceiling is sized correctly.** Experiment 3 is the only
experiment that reached the in-flight ceiling: 823 shed responses at a peak of 85 in flight against
the configured 90. That is the M6 finding F5 closed. Past the knee, requests are now refused with a
503 and a `Retry-After` instead of being lost at the connection level. Nothing needs tuning: the
ceiling was reached and not crossed, and after the F1 fix the same dependency outage arriving as a
refusal peaks at 3 in flight.

**F3 — a dead cache costs less than the capacity model assumed.** With Redis blackholed, MySQL
served 430 qps against 188 cached, which is 2.3 times the load rather than the ten times
[fault-tolerance-design.md](fault-tolerance-design.md) warns about, and it served them *faster* —
a query P95 of 1.54 ms against 1.89 ms cached. The reason is the M6 list projection: the queries
the cache stops absorbing are the cheap indexed ones, and the expensive `MEDIUMTEXT` reads left the
list path in M6. Request P95 moved 0.7 ms. No action; the row in
[capacity-scaling-model.md](../architecture/capacity-scaling-model.md) is now measured.

**F4 — the Redis breaker opens under MySQL pressure, and that is correct.** Experiments 3 and 4
both opened the *Redis* breaker while Redis was healthy — 4 and 13 transitions. Under a MySQL
outage the event loop holds tens of stalled requests, and a Redis call that waits behind them
exceeds the 2-second socket timeout. The instance then skips the cache for a moment and reads
MySQL, which is refusing anyway, so nothing downstream changes. It is visible on the Resilience
dashboard as a second breaker moving, and an operator reading it should look at MySQL first. No
change: raising the Redis timeout to hide it would delay every real Redis failure by the same
amount.

## Conclusion

The graceful-degradation NFR holds. Across five faults, the service never lost a correct answer it
could still give, and every answer it could not give carried a status code, a reason and a
`Retry-After`.

- **A dead cache is invisible to clients.** Zero failures, 0.7 ms of extra P95, and MySQL absorbing
  the whole read load at a lower query P95 than it served cached.
- **A dead database costs the uncached reads and nothing else.** Half the read traffic kept
  answering 200 from cache, and the rest answered 503 in under 5 ms rather than queueing.
- **Nothing hangs.** The worst tail measured under any fault, the 7.5 s `op_read_list` maximum in
  experiment 1, is a cache-aside read making several bounded calls in sequence — long, and bounded.
- **Recovery needs no operator.** Every experiment returned to zero 5xx within 65 seconds of the
  toxic being cleared, on the breaker's own half-open probe.

One defect was found and fixed, and it was the kind only a fault injection finds: a refused
connection answered 500 while the same dependency, blackholed, answered 503. The re-test is
experiment 4c.

Two caveats stay attached to every number above. One host runs the service, the dependencies, the
proxy and the generator, so absolute rates measure this laptop. And the degraded share under a
latency fault carries a 30-point noise floor, measured, not guessed. The comparisons in this report
share both error terms, which is why the report reads them as comparisons.
