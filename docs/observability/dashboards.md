# Dashboards Catalog

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-10

What each Grafana dashboard shows and how to read it. Add screenshots.

## Dashboards
| Dashboard | Purpose | Key panels | JSON model |
|---|---|---|---|
| API Overview (RED) | Service health | Rate, errors, P50/P95 latency | _link/path_ |
| Cache | Redis effectiveness | Hit ratio, evictions, latency | _..._ |
| Database | MySQL health | Query latency, connections, slow queries | _..._ |
| Resilience | Failure behavior | Timeouts, retries, circuit-breaker state | _..._ |

## Conventions
- Store dashboard JSON in-repo for reproducibility.
- Each panel should map to an SLI where possible ([slo.md](slo.md)).

## Screenshots
_Embed exported PNGs here for the portfolio report._
