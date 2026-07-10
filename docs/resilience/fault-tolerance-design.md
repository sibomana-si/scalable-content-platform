# Fault Tolerance & Resilience Design

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-10

Patterns for graceful degradation under failure (see `project_overview.txt`).

## Patterns Applied
| Pattern | Where | Configuration | Rationale |
|---|---|---|---|
| Timeouts | DB, Redis, outbound calls | _e.g. 200ms DB, 50ms cache_ | Bound tail latency |
| Retries w/ backoff | transient failures | _max attempts, jitter_ | Recover from blips without storms |
| Circuit breaker | DB / downstream | _thresholds, half-open_ | Stop cascading failures |
| Graceful fallback | cache outage → DB; DB outage → cached/empty | _per endpoint_ | Degrade, don't fail |
| Load shedding | overload | Redis-assisted | Protect core path |

## Degradation Matrix
| Failure | Expected behavior | User impact |
|---|---|---|
| Cache down | Serve from DB | Higher latency |
| DB slow | Timeout + retry/circuit-break | Some 504s, protected system |
| DB down | Serve cached reads; reject writes cleanly | Partial read availability |

## Validation
_Tested via failure injection — results in [chaos-test-report.md](chaos-test-report.md)._
