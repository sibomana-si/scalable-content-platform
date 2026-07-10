# Observability Guide

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-10

Covers the three pillars: logs, metrics, traces. Correlate all three via a shared `request_id`.

## Logging
- **Format:** structured JSON.
- **Standard fields:** `timestamp`, `level`, `message`, `request_id`, `user_id`, `route`, `latency_ms`, `status`.
- **Levels:** error-level for failures; avoid logging secrets/PII.

## Metrics (Prometheus)
Follow the **RED** method for the API and **USE** for resources.

| Metric | Type | Labels | Notes |
|---|---|---|---|
| `http_requests_total` | counter | `route`, `method`, `status` | Rate & errors |
| `http_request_duration_seconds` | histogram | `route`, `method` | P50/P95 latency |
| `cache_hits_total` / `cache_misses_total` | counter | `entity` | Hit ratio |
| `db_query_duration_seconds` | histogram | `query` | DB bottlenecks |

- Exposed at `GET /metrics`.

## Tracing (OpenTelemetry)
- End-to-end request traces with DB and cache spans.
- **Span naming:** `<component>.<operation>` (e.g. `articles.get`, `redis.get`, `mysql.select`).
- Propagate context; attach `request_id` to spans.

## What this enables
- Debugging slow endpoints, identifying DB bottlenecks, root-cause analysis.

## Related
- [SLO](slo.md) · [Dashboards](dashboards.md) · [Alerting](alerting-runbooks.md)
