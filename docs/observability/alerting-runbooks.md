# Alerting Runbooks

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-09-01

One runbook per alert: symptom → likely cause → diagnosis → remediation.

The rules themselves live in `prometheus/alerts.yml`. Each rule's `runbook_url` points at a
heading below, and `tests/unit/test_dashboards.py` fails the build if a rule links to a section
that does not exist, if it has no `for:` duration, or if it queries a metric the application does
not export.

**Alerting philosophy.** Alerts fire on **symptoms the user feels** — latency, errors,
unavailability — not on causes. Causes (a slow query, a saturated replica) are what the
dashboards are for, and appear here only where they are actionable on their own. Every rule has a
`for:` window so a single scrape blip cannot page anyone.

| Alert | Severity | Fires when | Sustained for |
|---|---|---|---|
| [HighReadLatencyP95](#alert-highreadlatencyp95) | warning | GET P95 > 200 ms | 10m |
| HighReadLatencyP99 | warning | GET P99 > 450 ms | 10m |
| [ElevatedErrorRate](#alert-elevatederrorrate) | warning | 5xx / total > 0.1% | 10m |
| [ErrorBudgetBurnFast](#alert-errorbudgetburnfast) | **critical** | 14.4× budget burn on 1h **and** 5m windows | 2m |
| [ErrorBudgetBurnSlow](#alert-errorbudgetburnslow) | warning | 6× budget burn on 6h **and** 30m windows | 15m |
| [HighDatabaseQueryLatency](#alert-highdatabasequerylatency) | warning | P95 SELECT > 50 ms | 10m |
| [TargetDown](#alert-targetdown) | **critical** | `up == 0` for the app job | 2m |
| [HighCpuSaturation](#alert-highcpusaturation) | warning | CPU > 85% of a core | 15m |
| [NoTrafficReceived](#alert-notrafficreceived) | info | request rate == 0 | 15m |
| [CacheUnavailable](#alert-cacheunavailable) | warning | Redis error rate > 0.1/s | 10m |
| [CircuitBreakerOpen](#alert-circuitbreakeropen) | **critical** | a breaker reads open | 2m |
| [ElevatedDegradedResponses](#alert-elevateddegradedresponses) | warning | degraded / total > 1% | 10m |
| [RequestsShed](#alert-requestsshed) | warning | any request refused at the in-flight ceiling | 5m |

---

## Alert: HighReadLatencyP95

- **Fires when:** `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{method="GET"}[5m]))) > 0.2` for **10m**.
  `HighReadLatencyP99` is the same shape at `0.99 > 0.45` — the tail can breach while P95 looks fine.
- **Why it matters:** the read path is the performance-critical path (PRD G1, [SLO](slo.md)).
- **Likely causes:** cache miss spike (from M5), a slow or unindexed query, resource saturation, a
  dependency timing out and being retried.
- **Diagnose:**
    1. **API Overview → "P95 latency by route"** — one endpoint or all of them? One endpoint points
       at a query or a code path; all of them points at saturation or a dependency.
    2. **Database → "Share of request time spent in the database"** — if it rose with the latency,
       the cause is below the app; if it did not, the cause is in the app or upstream.
    3. **Database → "Statements per request"** — a rising ratio is the signature of an N+1 pattern
       introduced by a recent change.
    4. **Traces** — filter on the slow route; the domain span (`articles.*`) and its child SQL span
       show where the time actually went. Pick a slow request's `request_id` out of the logs
       (`route`, `latency_ms`) and search traces for it.
- **Remediate:** scale out (the app is stateless, [ADR-0002](../architecture/adr/0002-modular-monolith.md));
  roll back a recent deploy if the step change lines up with it; add or fix the index
  ([indexing-strategy](../data/indexing-strategy.md)); from M5, warm the cache.

## Alert: ElevatedErrorRate

- **Fires when:** `sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) > 0.001` for **10m**.
- **Why it matters:** it is the availability SLO stated the other way round (99.9% ⇒ < 0.1% 5xx).
- **Diagnose:**
    1. **API Overview → "Responses by status class"** — a wall of 401/403 is an auth or client
       misconfiguration, not an outage, and does not consume the error budget.
    2. Filter the logs to `level=error` and group by `route`; each record carries `request_id`,
       `route`, `status` and the traceback.
    3. Correlate with recent deploys and migrations.
- **Remediate:** roll back first, diagnose second, if the onset matches a release. Otherwise
  mitigate the failing dependency and let the [resilience](../resilience/fault-tolerance-design.md)
  fallbacks absorb it.

## Alert: ErrorBudgetBurnFast

- **Fires when:** the 5xx ratio exceeds **14.4×** the 0.1% budget on **both** a 1h and a 5m window,
  for **2m**. Critical — this pages.
- **What the number means:** 14.4× burn exhausts 2% of the 30-day budget every hour; left alone for
  ~50 hours it consumes the entire month. See [slo.md](slo.md) § Error Budget.
- **Why two windows:** the long window establishes that the burn is real, the short one makes the
  alert *resolve* quickly once it stops — a long window alone would keep firing for an hour after
  the incident ended (Google SRE workbook, multi-window multi-burn-rate).
- **Diagnose:** as [ElevatedErrorRate](#alert-elevatederrorrate), but skip straight to the last
  change: at this burn rate, the cause is almost always a release or a dependency that just failed.
- **Remediate:** mitigate now, understand later. Then apply the budget policy: freeze risky changes
  until back within SLO, and run a postmortem if this single incident burned more than 20% of the
  budget.

## Alert: ErrorBudgetBurnSlow

- **Fires when:** the 5xx ratio exceeds **6×** the budget on **both** a 6h and a 30m window, for
  **15m**. Warning — ticket, not a page.
- **What it catches:** the steady drip that no instantaneous threshold notices but that still
  exhausts the month — 6× burn spends 10% of the budget every six hours.
- **Diagnose:** look for a *persistent* minority failure: one route, one client, one shard. The
  "Responses by status class" and per-route rate panels are the fastest split.
- **Remediate:** schedule the fix inside the budget; escalate to the fast-burn path only if the
  rate climbs.

## Alert: HighDatabaseQueryLatency

- **Fires when:** `histogram_quantile(0.95, sum by (le) (rate(db_query_duration_seconds_bucket{query="select"}[5m]))) > 0.05` for **10m**.
- **Why it matters:** this is a *cause* alert, kept because it is actionable before the user-facing
  latency SLO breaks. A single statement should stay an order of magnitude faster than the request
  containing it.
- **Diagnose:** **Database → "P95 latency by statement type"** and "Read / write mix"; then MySQL's
  own slow-query log and `EXPLAIN` on the suspect statement. The `query` label is only the SQL verb
  — the statement text is in the trace span (`db.statement`), never in a metric label.
- **Remediate:** add or fix the composite index ([indexing-strategy](../data/indexing-strategy.md));
  check for a missing `LIMIT` or a full scan introduced by a new filter; verify connection-pool size
  against concurrency.

## Alert: TargetDown

- **Fires when:** `up{job="content-platform"} == 0` for **2m**. Critical.
- **Diagnose:** distinguish "the process is gone" from "`/metrics` is unreachable". Check
  `/health/live` (pure liveness) and `/health/ready` (reports MySQL/Redis) — a ready-but-unscraped
  target is a network or service-discovery problem, not an application failure.
- **Remediate:** if liveness fails, let Kubernetes restart it and read the pod's last logs; if only
  the scrape fails, check the network policy and confirm `/metrics` is not being routed through the
  public gateway (it must not be — [ADR-0009](../architecture/adr/0009-observability-stack.md)).

## Alert: HighCpuSaturation

- **Fires when:** `rate(process_cpu_seconds_total{job="content-platform"}[5m]) > 0.85` for **15m**.
- **Why it matters:** the USE counterpart to the RED alerts. Saturation is what breaks the latency
  SLO next, so this is the one alert worth having *before* the symptom.
- **Diagnose:** **API Overview → "Saturation — CPU per replica"**. Is every replica saturated
  (genuine load — check the request-rate panel) or just one (a hot instance, or an unbalanced load
  balancer)?
- **Remediate:** scale out. If the request rate did not rise, look for a change that made the work
  per request more expensive rather than adding capacity.

## Alert: NoTrafficReceived

- **Fires when:** `sum(rate(http_requests_total[10m])) == 0` for **15m**. Info.
- **Why it exists:** the error-rate alerts divide by the request rate, so at zero traffic they are
  silent no matter how broken the service is. Expected in a quiet local or staging environment; in
  production it means requests are not arriving.
- **What counts:** `/health/live`, `/health/ready` and `/metrics` are excluded from
  `http_requests_total` (`PROBE_PATHS` in `app/observability/metrics.py`), so this measures real
  traffic. Were probes counted, Kubernetes and Prometheus would hold the denominator permanently
  above zero and this alert could never fire — in exactly the deployment where it matters.
- **Diagnose:** check the gateway/ingress and DNS before the app — the app being healthy while
  receiving nothing points upstream.
- **Remediate:** upstream routing; silence this alert in environments where idleness is normal.

## Alert: CacheUnavailable

- **Fires when:** `sum(rate(cache_errors_total[5m])) > 0.1` for **10m**.
- **Why it matters:** nothing is failing yet. Every read falls through to MySQL, which now
  carries the whole read load — around ten times its cached rate on the M6 measurement. The
  user-visible symptom arrives later, as latency, and then as an error rate.
- **Likely causes:** Redis is down, evicting under `maxmemory`, or reachable but slow enough to
  hit the command timeout.
- **Diagnose:** open **Cache (Redis)**. A climbing `cache_errors_total` with a collapsed hit
  ratio is Redis; a collapsed hit ratio with flat errors is a workload change. Check
  `/health/ready`: MySQL healthy and Redis failing returns 200 with `status: degraded`, which is
  the instance telling you it is still serving.
- **Remediate:** restart or reconnect Redis. Confirm the fallback engaged rather than the
  requests erroring — the cache is an optimization, never a dependency
  ([ADR-0004](../architecture/adr/0004-redis-cache-aside.md)).

## Alert: CircuitBreakerOpen

- **Fires when:** `max by (dependency) (circuit_breaker_state) == 2` for **2m**. Critical.
- **Why it is a cause and still pages:** every other rule in this file alerts on a symptom. This
  one names the cause of the 503s that follow, and it is actionable on its own — one dependency
  is down, and the service is already refusing calls to it.
- **Likely causes:** MySQL is down, blackholed, or slow enough that the timeout fires on every
  call. For `redis`, the same, with a smaller blast radius.
- **Diagnose:** open **Resilience**. Read the state panel for which dependency, then the
  timeouts panel for whether it is slow or gone. A breaker that opens and closes repeatedly is
  an intermittent fault, and the transitions panel shows it where the state panel does not.
- **Remediate:** fix the dependency. Do not restart the app to "reset" the breaker: it probes on
  its own after `BREAKER_RESET_SECONDS` and closes as soon as one probe succeeds. A
  restart only removes the evidence.

## Alert: ElevatedDegradedResponses

- **Fires when:** `sum(rate(degraded_responses_total[5m])) / clamp_min(sum(rate(http_requests_total[5m])), 0.0001) > 0.01`
  for **10m**.
- **Why it matters:** degradation is deliberate, so a small rate is the system working. A
  sustained 1% means the design is carrying load the capacity is not.
- **Diagnose:** split by `reason` on **Resilience → Degraded responses by reason**. Each reason
  has its own fix: `load_shed` means scale out, `breaker_open` means repair the dependency, and
  `upstream_timeout` means the dependency is slow rather than gone.
- **Remediate:** follow the reason. Compare this panel with the API Overview error rate — what
  appears on both is by design, and what appears only on the error rate is not.

## Alert: RequestsShed

- **Fires when:** `sum(rate(degraded_responses_total{reason="load_shed"}[5m])) > 0` for **5m**.
- **Why the threshold is zero:** shedding is the last defense, not a normal operating mode. An
  instance sheds only after it holds `MAX_INFLIGHT_REQUESTS` requests at once, which is derived
  from the measured knee, so any sustained shedding means capacity ran out.
- **Why it is not a page:** the callers who still get served keep their latency. That is the
  trade the ceiling buys, and it holds while you add capacity.
- **Diagnose:** **Resilience → Requests in flight against the shed ceiling**. In-flight sitting
  at the ceiling with shedding underneath is genuine saturation. Shedding with in-flight well
  below the ceiling means one instance is unbalanced, so check the load balancer.
- **Remediate:** scale out — the app is stateless by design
  ([ADR-0002](../architecture/adr/0002-modular-monolith.md)). Raise `MAX_INFLIGHT_REQUESTS`
  only against a new measurement: the default comes from the M6 knee on one machine, and a
  ceiling above what the instance can serve returns the queueing the ceiling exists to prevent.


---

_Template for new alerts: Fires when / Likely causes / Diagnose / Remediate. Add the rule to
`prometheus/alerts.yml` with a `for:`, a `severity`, and a `runbook_url` anchored at the new
heading — the tests enforce all four._
