# Runbook / Operations Manual

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-08-24

Day-2 operations reference.

## Routine Operations
| Task | Procedure |
|---|---|
| Start/stop locally | `docker compose up -d` / `docker compose stop <service>`. **Never `down`** — it removes MySQL, which declares no named volume |
| Scale replicas | _`kubectl scale` / HPA_ |
| Apply migrations | See [migrations.md](../data/migrations.md) |
| Rotate secrets | See [secrets-management.md](../security/secrets-management.md) |

## Health Checks
- Liveness: `GET /health`
- Readiness: `GET /ready` (verifies DB + cache reachable)

## Common Incidents
_Link to [alerting runbooks](../observability/alerting-runbooks.md). Add incident-specific procedures here as they arise._

## Performance Testing
To repeat a load measurement, follow the [load test runbook](../performance/load-test-runbook.md).
It covers the prerequisites, the four-run matrix, results collection, the checks that decide
whether a run is citable, and restoring the machine afterwards.

## Incident Response
- Severity definitions: _TBD_
- Postmortem template: _blameless; what happened, impact, timeline, root cause, action items._
