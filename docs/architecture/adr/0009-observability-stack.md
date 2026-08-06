# ADR-0009: structlog + prometheus-client + OpenTelemetry, correlated by `request_id`

> **Status:** Accepted · **Date:** 2026-08-01

## Context
[PRD](../../requirements/product-requirements.md) G5 asks for a production-debuggable system, and
the [NFR](../../requirements/non-functional-requirements.md) § Observability makes that concrete:
JSON logs for **100%** of requests correlated by `request_id`, RED metrics with P50/P95/P99,
OpenTelemetry traces carrying DB and cache spans, Grafana dashboards, and alerting on SLO
error-budget burn. The [observability guide](../../observability/observability-guide.md) had already
fixed the field names, metric names and label sets; what was missing was the decision about *how*
the three pillars are produced and joined.

Three forces shaped the choice:

- **The read path is the performance-critical path** ([ADR-0004](0004-redis-cache-aside.md), read
  P95 < 200 ms). Instrumentation sits directly on it, so it has to be cheap and it must not add a
  network round trip to a request.
- **The signal-flow diagram** (`diagrams/observability-flow.mmd`) states that telemetry export is
  best-effort: losing the collector must not fail or slow request handling.
- **Deployment is Kubernetes** ([deployment](../../operations/deployment.md)). Log files, local
  aggregation and pull-from-the-app designs are all wrong shapes for an ephemeral pod.

## Decision
We will produce the three pillars with **structlog** (JSON logs to stdout), **prometheus-client**
(a pull-based `/metrics` endpoint) and **OpenTelemetry** (push-based OTLP traces), and join them on
a single **`request_id`** minted by the outermost middleware.

Specifically:

1. **Logs** are structured JSON on **stdout only** — the platform ships them; the app never writes
   or rotates files. A redaction processor censors secret-bearing keys at any depth as defence in
   depth.
2. **`request_id`** is established by `RequestIDMiddleware`, the outermost layer, *before*
   authentication — so a request rejected at the auth layer is still correlatable. An inbound
   `X-Request-ID` is honoured only if it matches `[A-Za-z0-9._-]{1,64}`; anything else is replaced,
   because an unvalidated header echoed into a log line is a log-injection vector (CWE-117). The id
   is bound into the log context, set as a span attribute, returned in the response header, and
   quoted in the canonical error envelope.
3. **Metrics are pull-based**: `GET /metrics` is unauthenticated and excluded from the OpenAPI
   schema. Prometheus scrapes without credentials; the endpoint is protected at the **network**
   layer and is never routed through the public gateway. Every label value comes from a closed set
   or a router-supplied path template.
4. **Traces are push-based (OTLP) and off by default.** No `OTEL_EXPORTER_OTLP_ENDPOINT` means no
   provider, no exporter and no outbound connection attempt — which is also what keeps CI offline.
   When enabled, spans leave through a `BatchSpanProcessor`, asynchronously.
5. **Span names live at two layers.** Auto-instrumentation (FastAPI, SQLAlchemy, redis) keeps the
   **OTel semantic conventions** it emits — `GET /v1/articles/{article_id}`, `SELECT`, and the
   Redis command spans — because those are what every OTel-aware backend already understands, and
   they carry the DB/cache detail the NFR requires. On top of them, service-layer operations are
   wrapped in **`<component>.<operation>`** domain spans (`articles.get`, `auth.login`) so a trace
   reads as what the system was doing, not only which library it was in.

## Consequences
**Positive**
- One id joins a log line, a metric-bearing request and a trace, in any direction: the "why was
  this request slow" question is answerable end to end.
- Every pillar degrades independently and safely. With no collector the app is still fully logged
  and measured; `traced()` becomes a no-op context manager rather than an error.
- Metrics being pull-based means the app holds no delivery state, which suits stateless replicas
  ([ADR-0002](0002-modular-monolith.md)) and needs no push gateway.
- The bucketing functions that produce label values are pure, so cardinality is a unit-tested
  property rather than a code-review convention.

**Negative / costs accepted**
- The metrics registry is a **process-wide global**. Collectors must be declared at module scope
  once per process; the app factory registers nothing. Tests that need isolation build a private
  `CollectorRegistry` through the `build_metrics(registry)` factory.
- The tracer provider is likewise a once-per-process global, so `configure_tracing` reuses an
  installed SDK provider rather than replacing it.
- Two span-naming conventions coexist. This is a documentation cost (see the guide's Tracing
  section), accepted in exchange for not fighting the auto-instrumentation.
- `MetricsMiddleware`/`AccessLogMiddleware` are `BaseHTTPMiddleware` layers, which add a task
  hop per request. Acceptable against a 200 ms budget, and it is what lets the layers see requests
  that never reach the router.
- Cardinality is bounded by *collapsing* unmatched routes into `__unmatched__`, so a 404 storm is
  visible as a count but not attributable to individual paths — those remain in the logs.

**Neutral / follow-ups**
- `cache_hits_total` / `cache_misses_total` are specified but not emitted: there are no call sites
  until Redis cache-aside lands (M5). The Cache dashboard ships with its panels marked pending.
- Resilience metrics (timeouts, retries, circuit-breaker state) arrive with M7.
- Exemplars (linking a latency bucket to a trace id) are not enabled; they would need the
  OpenMetrics exposition format and a Grafana datasource configured for it.

## Alternatives Considered
| Option | Pros | Cons | Why rejected |
|---|---|---|---|
| stdlib `logging` with a JSON formatter | No new dependency; familiar | Context has to be threaded manually through every call; no contextvar merging | `request_id` on 100% of records is the requirement, and structlog's `contextvars` merging is exactly that feature |
| OpenTelemetry metrics instead of prometheus-client | One SDK for all three pillars | Push-based via the collector, an extra hop and a delivery dependency on the read path; less mature Prometheus-native tooling | Pull-based scraping is the simpler failure model and the one the C4 diagram already draws |
| Authenticate `/metrics` with a token or basic auth | Defence in depth if the endpoint is ever exposed | A shared credential to rotate and distribute to every scraper; still not a boundary control | Network-level protection is the standard Prometheus deployment and does not add a secret to manage |
| Rename auto-instrumentation spans to `<component>.<operation>` | One convention everywhere, exactly as the guide first described it | Loses the semantic-convention attributes backends key off; fragile monkey-patching per library | The guide was reconciled to describe both layers instead |
| Trace sampling always-on, exporting from every replica | Complete traces | Cost and volume at load; export in the request path | Off unless a collector is configured; sampling policy is a collector-side concern |
| Push metrics to a Pushgateway | Works for short-lived jobs | Wrong shape for long-lived replicas; the gateway becomes stale-metric state to manage | The app is a long-lived stateless service |
