# SLI / SLO / Error Budget

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-09


## Service Level Indicators (SLIs)
| SLI | Definition | Source metric |
|---|---|---|
| Latency | % of read requests served < 200ms | `http_request_duration_seconds` |
| Availability | % of requests not 5xx | `http_requests_total` |
| Error rate | 5xx / total | `http_requests_total` |

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
