# Dashboards Catalog

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-08-10

What each Grafana dashboard shows and how to read it.

## Dashboards

JSON models live in [`grafana/dashboards/`](https://github.com/si-sibomana/scalable-content-platform/tree/main/grafana/dashboards)
and are provisioned read-only (`allowUiUpdates: false`), so the repo is the source of truth.

| Dashboard | uid | Purpose | Key panels | JSON model | Status |
|---|---|---|---|---|---|
| API Overview (RED) | `scp-api-overview` | Service health and the SLO view | Availability, request rate, error ratio (5xx/4xx), P50/P95/P99, P95 by route, status classes, CPU saturation, error budget consumed | `grafana/dashboards/api-overview.json` | ✅ M4 |
| Database | `scp-database` | MySQL health | P95 SELECT latency, statements/s, statements **per request**, latency by statement type, read/write mix, share of request time in the DB | `grafana/dashboards/database.json` | ✅ M4 |
| Cache | `scp-cache` | Redis effectiveness | _Pending_ — hit ratio, hits/misses by entity, fallback rate | `grafana/dashboards/cache.json` | ⏳ M5 |
| Resilience | `scp-resilience` | Failure behavior | _Pending_ — timeouts, retries, circuit-breaker state, fallback rate | `grafana/dashboards/resilience.json` | ⏳ M7 |

**Why two dashboards ship empty.** `cache_hits_total`/`cache_misses_total` land with the
cache-aside read path (M5) and the resilience counters with the chaos work (M7). A panel querying
a metric nobody exports renders `No data` indefinitely, which is indistinguishable from an
outage — so each ships as a provisioned placeholder that states what it will contain and when.
`tests/unit/test_dashboards.py` enforces both halves of that rule: no panel may query an
unexported metric, and the two pending dashboards must say so.

## Conventions

- Store dashboard JSON in-repo for reproducibility. Edits made in the Grafana UI are **not**
  persisted; export the JSON and commit it.
- Every panel maps to an SLI where possible ([slo.md](slo.md)); thresholds on the latency and
  error panels are drawn at the SLO values, so "in budget" is visible without reading numbers.
- All panels reference the datasource uid `prometheus`, provisioned in
  `grafana/provisioning/datasources/`.
- Metric names, label sets and cardinality guards are fixed by the
  [observability guide](observability-guide.md); [ADR-0009](../architecture/adr/0009-observability-stack.md)
  records why.

## Bring-up (local)

The stack is behind a compose **profile**, so the default `docker compose up -d` remains exactly
what the integration tests need (MySQL + Redis) and nothing more.

```bash
docker compose --profile observability up -d     # + prometheus (9090) + grafana (3000)
uvicorn app.main:app --reload                    # the app runs on the host, not in compose
```

- **Prometheus** — <http://localhost:9090>. *Status → Targets* should show `content-platform` as
  **UP**; *Alerts* lists the rules from `prometheus/alerts.yml`.
- **Grafana** — <http://localhost:3000>. Anonymous admin access is enabled for local use only;
  the dashboards appear under the *Scalable Content Platform* folder.

The app runs on the host because there is no application image yet (it lands with the deployment
milestone), so Prometheus reaches it via `host.docker.internal` — see the `extra_hosts` entry in
`docker-compose.yml`. Generate some traffic before expecting panels to render:

```bash
curl -s localhost:8000/health/live >/dev/null
curl -s localhost:8000/v1/articles >/dev/null
curl -s localhost:8000/metrics | grep http_requests_total
```

After editing `prometheus/alerts.yml`, reload without a restart:
`curl -XPOST localhost:9090/-/reload`.

To add tracing to the picture, run an OTLP collector and set
`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`; with it unset the app is fully logged and
measured but emits no spans ([ADR-0009](../architecture/adr/0009-observability-stack.md)).

## Screenshots
_Embed exported PNGs here for the portfolio report (M8). Capture the API Overview dashboard
under load during M6 so the panels show real traffic rather than an idle service._
