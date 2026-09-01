# SLI / SLO / Error Budget

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-09-01


## Service Level Indicators (SLIs)
| SLI | Definition | Source metric | PromQL |
|---|---|---|---|
| Latency | % of read requests served < 200ms | `http_request_duration_seconds` | `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{method="GET"}[5m])))` |
| Availability | % of requests not 5xx | `http_requests_total` | `1 - ((sum(rate(http_requests_total{status=~"5.."}[5m])) or vector(0)) / sum(rate(http_requests_total[5m])))` |
| Error rate | 5xx / total | `http_requests_total` | `(sum(rate(http_requests_total{status=~"5.."}[5m])) or vector(0)) / sum(rate(http_requests_total[5m]))` |
 
The metrics are shipped (M4) — see [observability-guide § Metrics](observability-guide.md#metrics-prometheus).
Two implementation details the queries above depend on:

- **`status` is the code as a string**, so `status=~"5.."` selects server errors exactly.
- **The 5xx numerator is guarded with `or vector(0)`.** With no server errors in the window the
  filtered selector matches nothing, and in PromQL an empty vector divided by anything stays
  empty — so an unguarded ratio reports *no data* during precisely the healthy periods it is
  meant to confirm. The alert rules in `prometheus/alerts.yml` deliberately omit the guard: there
  the empty vector is the correct "nothing is wrong, do not fire".
- The latency histogram has **bucket boundaries at 0.2 s and 0.45 s**. `histogram_quantile`
  interpolates linearly *inside* a bucket, so a P95/P99 reading is only trustworthy when the
  target sits on a bucket edge; those two edges exist precisely because these are the targets.

## Service Level Objectives (SLOs)
| SLO | Target | Window | Measured at M6 |
|---|---|---|---|
| Read latency | 95% < 200ms (P99 < 450ms) | 30 days rolling | P95 **10.9 ms**, P99 **22.3 ms** |
| Availability | 99.9% | 30 days rolling | **100%** over 378,195 requests |
| Error rate | < 0.1% 5xx | 30 days rolling | **0.000%** — zero 5xx in every scenario |

Targets are the requirements set in [non-functional-requirements.md](../requirements/non-functional-requirements.md).

### What M6 measured, and what it did not

The Measured column comes from the steady scenario of the 2026-08-22 matrix: three replicas,
525 req/s, five minutes ([load-test-report](../performance/load-test-report.md)). Read it with
three limits.

- **A load test is not a rolling window.** These SLOs are 30-day objectives. A five-minute run
  proves the service can meet them, not that it does. The burn-rate alerts below remain the
  instrument that answers the SLO.
- **The latency objective has enormous margin, and the throughput budget has almost none.** Read
  P95 came in 18 times under the 200 ms line, while the ≥ 500 req/s target was met at 5% over.
  Tighten the latency SLO only against production traffic — the M6 profile is one shape of load on
  one host.
- **The error-rate SLI did not see a 5xx to count.** The application returned zero non-2xx
  responses in every scenario. Past the knee the *client* lost 1.9% of iterations at connection
  level, and none of that reaches `http_requests_total` (finding F5 in the
  [bottleneck analysis](../performance/bottleneck-analysis.md)). An availability SLI built on
  server-side counters cannot see a request the server never accepted.

**M7 closed that last gap.** `LoadShedMiddleware` gives the application the bound the paragraph
above asked for: at `MAX_INFLIGHT_REQUESTS` per instance the next request is refused with a 503
and a `Retry-After`, before it reaches a router. A refusal is a response, so it counts in
`http_requests_total` and in `degraded_responses_total{reason="load_shed"}`, and the loss past the
knee is now something the SLI can see. The ceiling comes from the M6 measurement itself — 450
req/s at the knee × the 0.2 s read SLO = 90 in flight, by Little's law — so the bound and the
number it protects share one derivation ([ADR-0012](../architecture/adr/0012-timeout-retry-and-circuit-breaker-policy.md)).
Two consequences for these SLOs. Shed responses are 5xx, so they burn the error budget, which is
correct: a refused request is a request the service failed to serve. And the error-rate SLI now
reports saturation instead of missing it, which makes `RequestsShed` a scaling signal rather than
a repair one.

The latency histogram bucket edges hold up under measurement: with P95 near 10 ms the reading
falls well inside the lowest buckets, so the 0.2 s and 0.45 s edges matter only when the service
is near its targets, which is exactly when they are needed.

## Error Budget
- Budget = `100% − SLO`. At 99.9% that is **0.1% → ~43 min/month**. The availability SLO and the "< 0.1% 5xx" error rate are the **same budget** expressed two ways.
- **Policy:** while the rolling-window budget remains, releases proceed normally. If the budget is exhausted, freeze risky changes (P0 fixes / security only) until back within SLO. If a single incident burns > 20% of the budget, run a postmortem. (Per the [Google SRE error-budget policy](https://sre.google/workbook/error-budget-policy/).)

### Burn-rate alerting

Alerting on the budget directly ("< 43 min left") is too late to act on, so the shipped rules
alert on **burn rate** — how fast the budget is being spent relative to a uniform month. Each uses
a **long window to confirm the burn and a short one to make the alert resolve promptly**; without
the short window an alert keeps firing long after the incident has stopped (Google SRE workbook,
multi-window multi-burn-rate).

| Alert | Burn rate | Long window | Short window | Budget spent | Severity |
|---|---|---|---|---|---|
| `ErrorBudgetBurnFast` | 14.4× | 1h | 5m | 2% per hour | **critical** — page |
| `ErrorBudgetBurnSlow` | 6× | 6h | 30m | 10% per 6h | warning — ticket |

Rules in `prometheus/alerts.yml`, procedures in
[alerting-runbooks.md](alerting-runbooks.md#alert-errorbudgetburnfast). Note that both are ratios,
so they are blind at zero traffic — `NoTrafficReceived` covers that gap.
