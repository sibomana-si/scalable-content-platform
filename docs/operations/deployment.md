# Deployment Guide

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-12

## Local (Docker Compose)
```bash
# bring up app + MySQL + Redis + observability stack
docker compose up --build
```
_Document service ports, health endpoints, and seed steps once compose exists._

## Build Image
```bash
docker build -t scalable-content-platform:local .
```

## Kubernetes
- Manifests/Helm chart location: _TBD_
- Deploy:
```bash
kubectl apply -f k8s/   # or: helm install ...
```
- Health/readiness probes: `GET /health` (liveness), `GET /ready` (readiness).
- Horizontal scaling: _HPA config / replica strategy._

## Configuration
All config via env vars — see [configuration-reference.md](configuration-reference.md). Secrets via [secrets-management.md](../security/secrets-management.md).

## Rollback
_Document rollback (image tag pinning, `kubectl rollout undo`, DB migration considerations)._
