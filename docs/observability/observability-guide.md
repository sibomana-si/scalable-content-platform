# Observability Guide

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-10

Covers the three pillars: logs, metrics, traces. Correlate all three via a shared `request_id`.

## Logging

> **Shipped** (M4) — `app/observability/logging.py` (structlog) + `AccessLogMiddleware`.

- **Format:** structured JSON, one line per event, on **stdout**. The app never writes log
  files; the platform ships them. `LOG_FORMAT=console` swaps in a human-readable renderer for
  local development.
- **Standard fields:** `timestamp`, `level`, `message`, `request_id`, `user_id`, `route`, `latency_ms`, `status`.
  The access-log record is `message: "http.request"` and additionally carries `method`.
  `route` is the **templated** path (`/v1/articles/{article_id}`); requests that matched no
  route collapse to the constant `__unmatched__` so caller-controlled paths never inflate
  cardinality (the same label feeds the metrics layer).
- **Levels:** `info` for < 400, `warning` for 4xx, `error` for 5xx (with the traceback);
  `LOG_LEVEL` sets the floor and an unparseable value falls back to `INFO` rather than
  failing boot.
- **Redaction:** a `redact_secrets` processor censors secret-bearing keys (`password`,
  `token`/`access_token`, `authorization`, `*_secret`, `credentials`, `cookie`, `api_key`) at
  any nesting depth, matching on **word boundaries** so `tokenizer` or `cache_key` survive
  intact. It is defence in depth, not a licence to log secrets — `JWT_SECRET` is additionally
  held as a `SecretStr` so it never reaches a `repr()`. Rejected bearer tokens are never
  logged, only the fact of the rejection.
- **Audit records:** `auth.failed` (a supplied token failed verification) and `auth.denied`
  (401/403 from the route policy) are emitted at `warning` with the `request_id`, satisfying
  the NFR § Security requirement that authentication failures are logged.

### Correlation

`RequestIDMiddleware` is the outermost layer. It accepts an inbound `X-Request-ID` when it
matches `[A-Za-z0-9._-]{1,64}` and mints a fresh 32-char hex id otherwise — an unvalidated
header value would let a caller forge log records (CWE-117 log injection). The id is:

1. bound into the structlog context, so every downstream record inherits it,
2. written to `request.state.request_id`, which is what the canonical error envelope's
   `request_id` field quotes,
3. echoed back in the `X-Request-ID` response header.

Because it is established *outside* authentication, even a `401` rejected at the middleware
layer is correlatable.

## Metrics (Prometheus)

> **Shipped** (M4) — `app/observability/metrics.py` + `MetricsMiddleware`, except the cache
> counters, which land with Redis cache-aside in M5.

Follow the **RED** method for the API and **USE** for resources.

| Metric | Type | Labels | Notes | Status |
|---|---|---|---|---|
| `http_requests_total` | counter | `route`, `method`, `status` | Rate & errors | ✅ M4 |
| `http_request_duration_seconds` | histogram | `route`, `method` | P50/P95 latency | ✅ M4 |
| `cache_hits_total` / `cache_misses_total` | counter | `entity` | Hit ratio | ⏳ M5 |
| `db_query_duration_seconds` | histogram | `query` | DB bottlenecks | ✅ M4 |

- Exposed at `GET /metrics`, unauthenticated (ADR-0009). The endpoint excludes itself from
  `http_requests_total`, so the scrape interval does not masquerade as traffic.

### Cardinality guards

Prometheus keeps one time series per label combination, so a caller-controlled label value is
an unbounded-memory hole in the scrape target. Every label here comes from a closed set:

- **`route`** is the router's **templated** path (`/v1/articles/{article_id}`), never the
  concrete URL. Requests that matched no route collapse to `__unmatched__` — a scanner walking
  random paths adds no series. When a middleware short-circuits (a 401/403 never reaches the
  router) the routing table is consulted directly, so denials are still attributed to the
  endpoint that was probed instead of vanishing into `__unmatched__`.
- **`query`** is the statement's leading SQL verb — `select`, `insert`, `update`, `delete` or
  `other` — parsed past comments and whitespace, with CTEs bucketed as `select`. Raw SQL as a
  label would be both unbounded *and* a way to leak literals into the exposition.
- **`status`** is the HTTP code as a string, which is what makes `status=~"5.."` work in the
  error-rate SLI.

### Histogram buckets

`http_request_duration_seconds` uses boundaries at 5 ms … 10 s including **0.2 s and 0.45 s**;
`histogram_quantile` interpolates within a bucket, so the P95 < 200 ms / P99 < 450 ms SLOs are
only measurable because those exact edges exist ([slo.md](slo.md)).
`db_query_duration_seconds` runs an order of magnitude finer (1 ms … 2.5 s).

### Failure counting

`MetricsMiddleware` sits below the access log and above authentication, and counts a request
even when it raises: an unhandled exception is recorded as a `500` before being re-raised for
the error handler. Otherwise the error-rate SLI would under-report exactly the failures that
matter most.

## Tracing (OpenTelemetry)
- End-to-end request traces with DB and cache spans.
- **Span naming:** `<component>.<operation>` (e.g. `articles.get`, `redis.get`, `mysql.select`).
- Propagate context; attach `request_id` to spans.

## What this enables
- Debugging slow endpoints, identifying DB bottlenecks, root-cause analysis.

## Related
- [SLO](slo.md) · [Dashboards](dashboards.md) · [Alerting](alerting-runbooks.md)
