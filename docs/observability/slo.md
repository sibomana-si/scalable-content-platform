# SLI / SLO / Error Budget

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-08-12


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
| SLO | Target | Window |
|---|---|---|
| Read latency | 95% < 200ms (P99 < 450ms) | 30 days rolling |
| Availability | 99.9% | 30 days rolling |
| Error rate | < 0.1% 5xx | 30 days rolling |

Targets are the requirements set in [non-functional-requirements.md](../requirements/non-functional-requirements.md); measured values are recorded after load testing (M6).

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
