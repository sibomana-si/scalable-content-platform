# ADR-0012: Timeout, retry and circuit breaker policy

> **Status:** Accepted · **Date:** 2026-08-29

## Context

M7 asks what the API does when a dependency fails. The M7 baseline measured the answer against a
real Toxiproxy fault, and it is recorded in the
[chaos test report](../../resilience/chaos-test-report.md): a blackholed MySQL held every request
open for minutes, and a latency toxic of 800 ms added itself to every request in full. Nothing
bounded either one, because nothing in the request path carried a ceiling except the pool wait.

Three ceilings were missing, and each fails differently:

1. **The connect.** aiomysql opens a socket with no connect timeout. A host that accepts nothing
   holds the caller until the kernel gives up, which is over two minutes.
2. **The statement.** MySQL runs a query until it finishes. A client that abandons the query
   alone leaves the server running it and the connection pinned to it, so the pool loses the
   connection for the length of the query, not the length of the timeout.
3. **The call.** Even with the first two, a caller needs one number it can promise: this call
   costs at most N seconds, retries included.

A timeout alone is not enough. It bounds one call and does nothing about the next thousand
callers each paying the same timeout against the same dead dependency, which is how a slow
dependency becomes a queue and the queue becomes an outage.

## Decision

Add `app/resilience/`, and route every repository call through it.

**The order is breaker, then timeout, then retry.** The breaker runs first, so an open circuit
costs no socket and no wait. The timeout wraps the retries, so the budget the caller was promised
covers every attempt: three attempts of a three-second call must not become a nine-second
request.

### The ceilings

| Setting | Default | What it bounds |
|---|---|---|
| `DB_CONNECT_TIMEOUT_SECONDS` | 5.0 | The TCP connect and MySQL handshake |
| `DB_STATEMENT_TIMEOUT_SECONDS` | 2.0 | The server-side kill, sent as `max_execution_time` |
| `DB_CALL_TIMEOUT_SECONDS` | 3.0 | The whole guarded call, retries included |
| `REDIS_SOCKET_TIMEOUT` | 2.0 | One cache command, and the whole cache call |
| `MAX_INFLIGHT_REQUESTS` | 90 | Requests one instance handles at once, before it sheds |

`DB_CALL_TIMEOUT_SECONDS` must exceed `DB_STATEMENT_TIMEOUT_SECONDS`, and `build_policies`
refuses to start otherwise. Inverted, the client abandons the query first and the connection
stays pinned to work nobody is waiting for — the exact failure the statement timeout exists to
prevent.

### The load-shed ceiling

`MAX_INFLIGHT_REQUESTS` is 90 per instance. Past it the instance answers 503 with a
`Retry-After` instead of accepting the request.

The number is derived, not chosen. The M6 load test measured one replica bending near
450 req/s, and the read SLO is a P95 under 200 ms. Little's law gives the queue length at
which that combination stops being possible:

```text
450 req/s × 0.2 s = 90 requests in flight
```

Past 90, the instance can still accept work, and every request it accepts breaks the latency
target for the requests it already holds. Refusing is the honest answer.

**Where the number came from, because it does not travel.** One laptop, an i7-12700H held at
the `performance` power profile, with the k6 generator pinned to the eight E-cores and the six
P-cores left to the system under test. The same ceiling on a smaller container sheds traffic
the instance could have served, and on a larger one it queues past the SLO. Re-derive it from a
measurement of the target machine, and keep it an environment variable — never a constant.

**Reject, do not queue.** The counter is a plain integer, not a semaphore. A semaphore makes
the caller wait for a slot, and a waiting request is the thing the ceiling exists to prevent:
it holds memory, it holds a connection, and it disappoints the caller later instead of now.
This closes M6 finding F5, where requests past the knee were lost at the connection level, with
no server-side signal naming the cause.

**Probes and the metrics scrape are exempt.** A shed liveness probe is a restart. A shed
readiness probe removes an instance that is still serving. A shed scrape is a gap in the data
over exactly the window that explains the incident.

### Reads retry. Writes do not.

`DB_RETRY_ATTEMPTS` defaults to 2, which is one retry. It applies to reads only. A read is
idempotent, so a retry costs a round trip and risks nothing. A retried write is a second write,
and nothing downstream can tell the two apart. The rule is data rather than a branch: the write
policy carries `attempts == 1`, so `guarded_write` cannot retry even by mistake.

A retry needs one more thing to be legal. A failed statement leaves the session unusable, so
`guarded_read` passes `session.rollback` as the hook that runs between attempts. Without it the
second attempt raises `PendingRollbackError` and proves only that the session is broken. The
rollback discards the request transaction, and that costs nothing: the fault that triggered the
retry already invalidated the connection, so the transaction was gone before the hook ran.

The waits use **full jitter** — each is drawn from `[0, min(base * 2**n, cap)]`. Waiting the full
exponential every time re-synchronizes every replica onto the same instant, which is the stampede
the backoff exists to prevent.

Only a fault is retried. `is_transient` retries a lost connection, a refused socket and a
timeout. It never retries an answer, including every `DomainError` and every constraint
violation: the second attempt breaks the same constraint one round trip later.

### The breaker

Five consecutive failures open the circuit for 10 seconds, after which one probe decides. A
success closes it; a failure re-opens it and restarts the clock. `BREAKER_HALF_OPEN_MAX_CALLS`
defaults to 1, because a herd of probes against a dependency that just came back is how a
recovery becomes a second outage.

The breaker counts *consecutive* failures. Two failures an hour apart are not an outage, so a
success resets the count.

A domain error never reaches the breaker. Ten thousand 404s are ten thousand answers, and a
breaker that opens on them takes the API down over queries that worked.

### tenacity is in. pybreaker is out.

`tenacity` provides `AsyncRetrying`, `stop_after_attempt`, `retry_if_exception` and
`wait_random_exponential`, which is full jitter already written and already tested. It stays.

`pybreaker` is dropped from `requirements.txt`. The breaker here is a 60-line state machine over
an injected `time.monotonic`, and the injection is the point: the state machine is a pure
decision, so its tests drive the clock directly instead of waiting on it. `pybreaker` brings a
storage abstraction, a listener protocol and a threading model, and this process is a
single-threaded event loop with one breaker per dependency. The dependency was cost with no
matching gain.

## Consequences

**Positive**

- A dependency failure costs a bounded, configured amount of time and then becomes an answer.
- The failure is visible: `dependency_timeouts_total`, `dependency_retries_total`,
  `circuit_breaker_state` and `circuit_breaker_transitions_total` say which dependency, how
  often, and what the breaker did about it.
- The breaker state machine is tested deterministically, so the suite gains no wall-clock waits.
- One less production dependency to audit and bump.

**Negative**

- **A ceiling that is too tight is now a way to fail.** A slow query that used to finish in
  4 seconds now raises at 2. This is the intended trade, and `DB_STATEMENT_TIMEOUT_SECONDS` is
  the knob, but it is a behavior change under load rather than at review time.
- The guard adds a layer between the service and the repository. A traceback from a failing query
  now passes through `guarded_call`, and the original driver error is the `__cause__` rather than
  the exception itself.
- The breaker is per process. Three replicas hold three breakers and open them independently, so
  the first replica to notice an outage does not spare the other two their own five failures.
- **The shed ceiling is per instance and machine-specific.** A default derived on one laptop is
  wrong on any other machine, in either direction: too low sheds traffic the instance could
  serve, and too high returns the queueing the ceiling removes. It is configuration that must be
  re-measured on deployment, and nothing in the code can detect that it was not.
- `max_execution_time` covers read-only `SELECT` statements. Writes are bounded by the client
  call timeout and by `innodb_lock_wait_timeout`, not by this setting.

## Alternatives

| Option | Why not |
|---|---|
| `pybreaker` | A storage abstraction, a listener protocol and a threading model this process does not use. Its clock is not injectable, so the state machine's tests would wait on real time. |
| Retry writes too | Nothing downstream can tell a retried write from two writes. `create` has no compare-and-set to make the duplicate harmless. |
| A timeout per attempt instead of over the call | The caller cannot be promised a number. Three attempts of a three-second call is a nine-second request, whatever the configuration says. |
| A shared breaker in Redis | The breaker exists to survive a dependency outage. Putting its state in another dependency makes a Redis outage a database outage. |
| No breaker, timeouts only | Measured in the M7 baseline: every caller pays the full timeout, and the pool stays saturated for as long as the dependency is down. |
| A semaphore for the shed ceiling | It queues. A caller that waits for a slot holds memory and a connection, and learns it failed later instead of now. |
| A fixed shed ceiling in code | The right number is a property of the machine, not of the design. Compiled in, it is wrong everywhere except the laptop it was measured on. |
| Failing readiness when Redis is down | It removes every replica at once during a cache outage, which turns a slower service into no service. The cache is an optimization ([ADR-0004](0004-redis-cache-aside.md)). |

## Validation

| Claim | Test |
|---|---|
| Every unbounded or self-contradictory setting is refused by name | `tests/unit/test_resilience_policy.py` |
| The call timeout must exceed the statement timeout | `tests/unit/test_resilience_policy.py` |
| A transient fault is retried, and an answer is not | `tests/unit/test_retry.py` |
| The backoff is exponential, capped, and jittered | `tests/unit/test_retry.py` |
| The write policy never retries | `tests/unit/test_retry.py`, `tests/unit/test_guard.py` |
| The breaker opens, probes, closes and re-opens on schedule | `tests/unit/test_breaker.py` |
| An open breaker issues no call | `tests/unit/test_guard.py` |
| A timeout counts as a breaker failure, and a domain error does not | `tests/unit/test_guard.py` |
| A guarded read rolls the transaction back between attempts | `tests/unit/test_guard.py` |
| The engine carries a connect timeout and `max_execution_time` | `tests/unit/test_db_engine_timeouts.py` |
| MySQL kills a runaway query and the connection returns to the pool | `tests/integration/test_db_timeout.py` |
| A request under the ceiling passes, and the next one is shed with a `Retry-After` | `tests/unit/test_load_shed.py` |
| The in-flight gauge returns to zero, including when the handler raises | `tests/unit/test_load_shed.py` |
| Probes and the metrics scrape are never shed | `tests/unit/test_load_shed.py` |
| A timeout answers 504 and an open circuit answers 503, both with `Retry-After` | `tests/unit/test_error_envelope_resilience.py` |
| A cache hit survives a database outage, and a miss refuses rather than fails | `tests/unit/test_article_service_fallback.py` |
| A dead cache leaves the instance ready, and a dead database does not | `tests/integration/test_health_ready_degraded.py` |
