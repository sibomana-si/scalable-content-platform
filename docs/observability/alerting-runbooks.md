# Alerting Runbooks

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-10

One runbook per alert: symptom → likely cause → diagnosis → remediation.

## Alert: High P95 Latency
- **Fires when:** read P95 > 200ms for N minutes.
- **Likely causes:** cache miss spike, slow DB query, resource saturation.
- **Diagnose:** check cache hit ratio, DB query latency panel, traces for slow spans.
- **Remediate:** _scale replicas, warm cache, investigate slow query._

## Alert: Elevated Error Rate
- **Fires when:** 5xx rate > threshold.
- **Diagnose:** logs filtered by `request_id`/route; recent deploys.
- **Remediate:** _rollback, mitigate upstream._

## Alert: Cache Unavailable
- **Fires when:** Redis errors > threshold.
- **Expected behavior:** degrade to DB (see [resilience](../resilience/fault-tolerance-design.md)).
- **Remediate:** _restart/reconnect; verify graceful fallback engaged._

_Template for new alerts: Fires when / Likely causes / Diagnose / Remediate._
