# Chaos / Failure-Injection Report
### Validates [fault-tolerance-design.md](fault-tolerance-design.md).

> **Status:** 🟨 Baseline recorded — the four experiments run against the resilient build in the last commit of M7 · **Owner:** Simon Sibomana · **Last updated:** 2026-08-31

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
| 0 | None, through the proxy | The proxy hop is small and stable | _..._ | 🟥 |
| 1 | MySQL latency, sized against the statement timeout | Timeouts trip inside the bound, retries stay bounded | _..._ | 🟥 |
| 2 | Redis unavailable | Fall through to MySQL, elevated latency, the breaker opens once | _..._ | 🟥 |
| 3 | MySQL blackholed | The breaker opens, 503 returns fast, no queue builds | _..._ | 🟥 |
| 4 | MySQL unavailable | Cached reads served, writes rejected cleanly, no 500s | _..._ | 🟥 |

## Evidence

_Run summaries and machine-state snapshots under `docs/resilience/results/`._

## Findings & Improvements

- _What broke against expectation, what was tuned, and the re-test result._

## Conclusion

_System behavior against the graceful-degradation NFR._
