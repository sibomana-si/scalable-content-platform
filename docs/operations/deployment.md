# Deployment Guide

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-08-24

## Local (Docker Compose)
The compose file has four layers, each behind its own profile, so the plain command stays
exactly what the integration test suite needs.

```bash
docker compose up -d                                        # mysql + redis only
docker compose --profile observability up -d                # + prometheus (9090) + grafana (3000)
docker compose --profile scale up -d --build --scale app=3  # + app replicas behind nginx (8080)
docker compose --profile load run --rm k6 run /scripts/selftest.js   # the load generator
```

The `load` profile is `run`-only. It starts nothing in the background, because a load generator
that outlives its run is a service nobody asked for.

| Service | Port | Notes |
|---|---|---|
| `mysql` | 3307 → 3306 | 3307 on the host, so it does not collide with a local MySQL |
| `redis` | 6379 | |
| `prometheus` | 9090 | `observability` profile |
| `grafana` | 3000 | `observability` profile, anonymous admin, local only |
| `nginx` | 8080 → 80 | `scale` profile; the only way into the app replicas |
| `app` | none published | `scale` profile; reachable through nginx only |
| `k6` | none | `load` profile, `run`-only; pinned to the E-cores with `cpuset` |

**Never run `docker compose down`.** It removes every service in the project, including MySQL,
which declares no named volume — the data does not come back. Stop what you named instead:

```bash
docker compose stop app nginx
```

### Running a load test

The load generator runs in the same compose project, on its own profile, and writes its summaries
into `docs/performance/results/`. The [load test runbook](../performance/load-test-runbook.md)
carries the full procedure, including the machine-state capture that decides whether a result is
citable.

```bash
scripts/run_load_matrix.sh 2026-08-22-baseline
```

### Applying migrations

Migrations do not run at container start. A replica set would race, and a schema change that
fails mid-rollout is worse than one that fails before it. Run them once, against a stack that
is already up:

```bash
alembic upgrade head
```

## Building the image
```bash
docker build -t scalable-content-platform:local .
```

Multi-stage: the build stage compiles wheels and the runtime stage carries none of the
toolchain. Notes that matter when you change it:

- The base is `python:3.11-slim`, matching the CI interpreter. A green CI run and a running
  container then agree about the language version.
- Dependencies install from `requirements.lock`, the frozen resolved set, so a rebuild months
  later installs the versions the image was tested with.
- The process runs as the non-root user `appuser` (uid 10001) and owns nothing it runs.
- `HEALTHCHECK` calls `/health/live`, which has no dependencies. A container that fails it is
  genuinely broken rather than waiting on MySQL. Readiness stays the orchestrator's job.
- `.dockerignore` keeps `.env`, `.venv/`, `.git/`, `tests/`, and `docs/` out of the context.
  The `.env` line is the load-bearing one: it holds real credentials.

One uvicorn worker per container. Scaling is horizontal — more replicas, not more workers — so
the orchestrator sees real per-instance load and can act on it.

## Verifying scale-out

```bash
docker compose --profile scale up -d --build --scale app=3
pytest -m scale
```

The suite skips unless `localhost:8080` is reachable, so it is safe to run without the stack.
It proves the properties that only appear across real containers: every replica reaches MySQL
and Redis by service name, a write on one replica is readable from any other, an update is
never stale on another replica, a JWT minted by one is accepted by all, and the balancer
actually spreads the load. See
[capacity-scaling-model](../architecture/capacity-scaling-model.md) for the replica ceiling.

CI does not build the image, so `-m scale` skips there. Adding an image build to CI is
deployment work (M8).

## Kubernetes
- Manifests/Helm chart location: _TBD_
- Deploy:

```bash
kubectl apply -f k8s/   # or: helm install ...
```
- Health/readiness probes: `GET /health/live` (liveness), `GET /health/ready` (readiness).
  Set the readiness probe's `timeoutSeconds` to at least `READINESS_TIMEOUT_SECONDS` (default
  2s): the endpoint bounds each dependency check itself, and a shorter orchestrator timeout
  just abandons a handler that is still working.
- **Horizontal scaling.** The application is stateless — no server-side session, no
  in-process cache, no request affinity — so replicas scale on CPU or request rate with no
  sticky routing. The ceiling is the database connection count, not the application: each
  replica opens up to `DB_POOL_SIZE + DB_MAX_OVERFLOW` (15 by default), which puts about eight
  replicas under MySQL's default `max_connections` of 151. Shrink the pools or front MySQL
  with a pooler beyond that; the arithmetic is in
  [capacity-scaling-model](../architecture/capacity-scaling-model.md) and is executable in
  `tests/unit/test_capacity_model.py`.
- Set `JWT_SECRET` identically across replicas. A token minted by one replica must verify on
  every other, and per-replica secrets would turn scale-out into random 401s.

## Configuration
All config via env vars — see [configuration-reference.md](configuration-reference.md). Secrets
via [secrets-management.md](../security/secrets-management.md).

## Rollback

_Document rollback (image tag pinning, `kubectl rollout undo`, DB migration considerations)._
