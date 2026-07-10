# Secrets Management

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-10

## Principles
- Secrets never committed to source control (`.env` is gitignored; commit `.env.example` with empty values).
- Config via environment variables — see [configuration-reference.md](../operations/configuration-reference.md).

## Sources by Environment
| Environment | Source |
|---|---|
| Local dev | `.env` file (gitignored) |
| CI | CI secret store |
| Kubernetes | K8s `Secret` objects (consider sealed-secrets / external secrets) |

## Secrets Inventory
| Secret | Used by | Rotation |
|---|---|---|
| `JWT_SECRET` / signing key | Auth | _cadence_ |
| `MYSQL_PASSWORD` | DB connection | _cadence_ |
| `REDIS_PASSWORD` | Cache connection | _cadence_ |

## Rotation
_Document rotation procedure and blast radius for each secret._
