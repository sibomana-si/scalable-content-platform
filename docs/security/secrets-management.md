# Secrets Management

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-27

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
| `JWT_SECRET` / signing key | Auth: HS256 access-token signing/verification | 90 days, or immediately on suspected compromise |
| `MYSQL_PASSWORD` | DB connection | _cadence_ |
| `REDIS_PASSWORD` | Cache connection | _cadence_ |

Loaded into the app as a `SecretStr` (`app/config.py`) so it never appears in `repr()` or logs, and 
read only via `Settings.require_signing_key()`, which raises if the key is unset, the app refuses to 
sign or verify tokens rather than falling back to an empty key.

## Rotation
**`JWT_SECRET` (HS256).** Because it is a single symmetric key with no key-id in the token header,
rotation invalidates all outstanding access tokens:

1. Generate a new high-entropy key (e.g. `openssl rand -base64 48`).
2. Update the secret in the environment's store (K8s `Secret` / CI store; `.env` locally) and restart
   the API pods.
3. **Blast radius:** every unexpired access token (≤ 15-min TTL) is rejected on the next request,
   forcing re-login. There is no refresh token, so the impact window is bounded by the token TTL.
   Schedule rotations off-peak or roll pods gradually to smear re-authentication.

_Graceful (zero-logout) rotation would require overlapping keys keyed by a `kid` header, deferred, not 
needed at MVP given the short token TTL._
