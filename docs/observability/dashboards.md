# Dashboards Catalog

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-09-01

What each Grafana dashboard shows and how to read it.

## Dashboards

JSON models live in [`grafana/dashboards/`](https://github.com/si-sibomana/scalable-content-platform/tree/main/grafana/dashboards)
and are provisioned read-only (`allowUiUpdates: false`), so the repo is the source of truth.

| Dashboard | uid | Purpose | Key panels | JSON model | Status |
|---|---|---|---|---|---|
| API Overview (RED) | `scp-api-overview` | Service health and the SLO view | Availability, request rate, error ratio (5xx/4xx), P50/P95/P99, P95 by route, status classes, CPU saturation, error budget consumed | `grafana/dashboards/api-overview.json` | ✅ M4 |
| Database | `scp-database` | MySQL health | P95 SELECT latency, statements/s, statements **per request**, latency by statement type, read/write mix, share of request time in the DB, connection pool by state, pool utilization | `grafana/dashboards/database.json` | ✅ M4 (pool panels M5) |
| Cache | `scp-cache` | Redis effectiveness | Hit ratio, degraded operations, hit ratio by entity, hits/misses by entity, degraded operations by type, database read rate | `grafana/dashboards/cache.json` | ✅ M5 |
| Resilience | `scp-resilience` | Failure behavior | Circuit-breaker state and transitions by dependency, timeouts per dependency, retry rate with the retry success ratio, degraded responses by reason, requests in flight against the shed ceiling | `grafana/dashboards/resilience.json` | ✅ M7 |

**Every dashboard now queries a live metric.** Resilience shipped as a provisioned placeholder
from M4 to M6, because a panel querying a metric nobody exports renders `No data` indefinitely,
which is indistinguishable from an outage. M7 exported the counters and filled the panels.
`tests/unit/test_dashboards.py` enforces both halves of the rule: no panel may query an unexported
metric, and a pending dashboard must say it is pending. The pending list is now empty.

**Reading the Resilience dashboard.** It answers one question the RED dashboards cannot: is the
service degrading on purpose? A 503 from an open breaker and a 503 from a crash look identical on
an error-rate graph, and they need opposite responses. Read the breaker state first — 0 closed,
1 half-open, 2 open — then `degraded_responses_total` by reason, which names the fix.
`reason="upstream_timeout"` points at a slow dependency, `reason="breaker_open"` at a dead one,
and `reason="load_shed"` at an instance that ran out of capacity, which is a scaling answer rather
than a repair. The in-flight panel draws `MAX_INFLIGHT_REQUESTS` as a threshold, so the distance
to the shed ceiling is visible before the shedding starts. Four alerts read the same series — see
[alerting-runbooks](alerting-runbooks.md).

**Reading the Cache dashboard.** Take the hit ratio and the degraded-operation rate together. A
falling hit ratio with a flat error rate is a workload change. A falling hit ratio with a climbing
error rate is a Redis problem. Judge the two entities separately: article detail carries the
≥ 90% target, and unfiltered list pages bust on every write by design
([ADR-0010](../architecture/adr/0010-generation-counter-list-invalidation.md)), so a low list
ratio under a write-heavy load is expected.


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
- **A ratio whose numerator filters on a label must guard it with `or vector(0)`** — e.g.
  `(sum(rate(http_requests_total{status=~"5.."}[5m])) or vector(0)) / sum(rate(...))`. Without the
  guard, a window containing no matching responses selects an *empty vector*, and an empty vector
  divided by anything stays empty, so the panel reads "No data" during precisely the healthy
  periods it exists to confirm. Alert rules are the exception: there the empty vector is the
  correct "nothing is wrong, do not fire". Enforced by `tests/unit/test_dashboards.py`.
- **Query variables need `refresh: 1|2` and, if they offer "All", an `allValue`.** These dashboards
  are provisioned from JSON and never saved through the UI, so nothing populates a variable's
  `options` array; Grafana's default `refresh: 0` would leave the list empty and expand `$route` to
  nothing. Also enforced by `tests/unit/test_dashboards.py`.

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
