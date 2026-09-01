# Fault Tolerance & Resilience Design

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-09-01

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
| Graceful fallback | Cache outage → MySQL; MySQL outage → the cache, then 503 | Per endpoint, no configuration | An answer from the cache is still an answer; a refusal beats a hang |
| Load shedding | Every route except the probes and the metrics scrape | 90 in flight per instance (`MAX_INFLIGHT_REQUESTS`), `Retry-After: 1` (`SHED_RETRY_AFTER_SECONDS`) | Derived from the M6 knee by Little's law: 450 req/s × the 0.2 s read SLO. Past it the SLO cannot be met, so the instance refuses rather than queues |
| Degraded readiness | `/health/ready` | No configuration | Only MySQL decides the status code. Failing readiness on Redis would remove every replica at once during a cache outage |

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

## Degradation matrix

Every row answers with a status a client can act on. Nothing in this table is a 500: a 500 says
the server did something it did not intend, and none of these are that.

| Failure | Expected behavior | Status | User impact |
|---|---|---|---|
| Redis down | Fall through to MySQL on every read, and drop invalidation | 200 | Higher latency. MySQL takes the whole read load, roughly ten times its cached rate |
| MySQL slow | The statement timeout kills the query, one retry follows, then the call gives up | 504 `UPSTREAM_TIMEOUT` with `Retry-After` | A slow read fails at 3 s instead of hanging. The connection returns to the pool |
| MySQL down, cache hit | Serve the cached body without touching the repository | 200 | None, for anything already cached |
| MySQL down, cache miss | The breaker opens after five failures and refuses at once | 503 `SERVICE_UNAVAILABLE` with `Retry-After` | Uncached reads fail fast rather than each paying a 3-second timeout |
| MySQL down, write | Refused before the statement runs | 503 `SERVICE_UNAVAILABLE` | No writes. Authorization and compare-and-set never read the cache, so nothing proceeds on stale data |
| Instance at capacity | Shed at the in-flight ceiling, before the router | 503 `SERVICE_UNAVAILABLE` with `Retry-After` | The callers already being served keep their latency |
| Redis down, MySQL up, at the probe | Readiness reports the cache and keeps the instance in the pool | 200, `status: degraded` | None. The instance still serves every read |
| MySQL down, at the probe | Readiness fails | 503, `status: unready` | The instance leaves the pool |

**Why 503 and 504 are different codes.** A dependency that answers late and one that refuses need
different fixes, and the split survives all the way to the counter: `degraded_responses_total`
carries `reason="upstream_timeout"`, `reason="breaker_open"` or `reason="load_shed"`. One
"degraded" count would need an operator to guess which of the three fixes applies.

## Observability

| Series | What it answers |
|---|---|
| `dependency_timeouts_total{dependency}` | Which dependency stopped answering, and how often |
| `dependency_retries_total{dependency,outcome}` | Whether the retries fixed anything |
| `circuit_breaker_state{dependency}` | What the breaker is doing now: 0 closed, 1 half-open, 2 open |
| `circuit_breaker_transitions_total{dependency,to_state}` | When it moved, and which way |
| `degraded_responses_total{route,reason}` | That degradation engaged, on which route, and why |
| `inflight_requests` | How many requests the instance holds, against the ceiling it sheds at |

All six are on the **Resilience** dashboard, and four alerts read them:
`CircuitBreakerOpen`, `ElevatedDegradedResponses`, `RequestsShed` and `CacheUnavailable` — see
[alerting-runbooks](../observability/alerting-runbooks.md).

## Validation
_Tested via failure injection — results in [chaos-test-report.md](chaos-test-report.md)._
