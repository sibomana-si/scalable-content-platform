# Fault Tolerance & Resilience Design

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-08-31

Patterns for graceful degradation under failure (see `project_overview.txt`).

## Patterns Applied

The numbers below are the defaults in `.env.example`. Each one is a ceiling, and
`app/resilience/policy.py` refuses an unbounded or self-contradictory set at start-up. The
decision and the reasoning are in
[ADR-0012](../architecture/adr/0012-timeout-retry-and-circuit-breaker-policy.md).

| Pattern | Where | Configuration | Rationale |
|---|---|---|---|
| Connect timeout | MySQL | 5.0 s (`DB_CONNECT_TIMEOUT_SECONDS`) | aiomysql defaults to no timeout, so a blackholed host holds the request for minutes |
| Statement timeout | MySQL | 2.0 s, as `max_execution_time` (`DB_STATEMENT_TIMEOUT_SECONDS`) | The server kills the query, so the connection returns to the pool |
| Call timeout | Every guarded call | 3.0 s (`DB_CALL_TIMEOUT_SECONDS`), 2.0 s for Redis (`REDIS_SOCKET_TIMEOUT`) | One number the caller can be promised, retries included |
| Retries w/ backoff | Reads only, transient faults only | 2 attempts, full jitter over `[0, min(0.05 * 2**n, 0.5)]` | Recover from a blip without a storm; a retried write is a second write |
| Circuit breaker | MySQL, Redis | 5 consecutive failures, 10 s reset, 1 half-open probe | An open circuit costs no socket and no wait, so a dead dependency stops queueing callers |
| Graceful fallback | cache outage → DB; DB outage → cached/empty | _per endpoint_ | Degrade, don't fail |
| Load shedding | overload | Redis-assisted | Protect core path |

### Order of composition

`app/resilience/guard.py` composes them in one order, and the order is the design:

1. **The breaker first.** An open circuit answers without a socket. A breaker consulted after
   the timeout saves nothing.
2. **The timeout next.** It wraps every attempt, so three attempts of a three-second call is
   still a three-second request.
3. **The retry innermost.** Reads take `guarded_read`, which rolls the session back between
   attempts. Writes take `guarded_write`, which never retries.

An answer is not a fault. A `DomainError` — a 404, a 409 — passes through the guard untouched
and never reaches the breaker.

## Degradation Matrix
| Failure | Expected behavior | User impact |
|---|---|---|
| Cache down | Serve from DB | Higher latency |
| DB slow | Timeout + retry/circuit-break | Some 504s, protected system |
| DB down | Serve cached reads; reject writes cleanly | Partial read availability |

## Observability

| Series | What it answers |
|---|---|
| `dependency_timeouts_total{dependency}` | Which dependency stopped answering, and how often |
| `dependency_retries_total{dependency,outcome}` | Whether the retries fixed anything |
| `circuit_breaker_state{dependency}` | What the breaker is doing now: 0 closed, 1 half-open, 2 open |
| `circuit_breaker_transitions_total{dependency,to_state}` | When it moved, and which way |

## Validation
_Tested via failure injection — results in [chaos-test-report.md](chaos-test-report.md)._
