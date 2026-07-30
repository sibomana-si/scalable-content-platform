# Authentication & Authorization

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-27

## Authentication (JWT)
- **Token type:** short-lived access token (JWT), sent as `Authorization: Bearer <token>`.
- **Claims:** `sub` (user id), `role`, `iat`, `exp`. **No `jti`** — tokens are stateless with no denylist (see [ADR-0005](../architecture/adr/0005-stateless-jwt-auth.md)); the short TTL is the revocation strategy.
- **Signing:** **HS256** with a single symmetric key from `JWT_SECRET` (env only; loaded as `SecretStr`, read via `Settings.require_signing_key()`, which fails closed if unset). Key management and rotation/blast-radius: [secrets-management.md](secrets-management.md).
- **Password storage:** **Argon2id** (`argon2-cffi` defaults); hashes embed a random salt. Never plaintext, returned, or logged.
- **Expiry / refresh:** 15-minute access-token TTL (`JWT_EXPIRE_SECONDS`, default 900); **no refresh token in MVP** (clients re-authenticate on expiry). Short TTL bounds the blast radius of a non-revocable bearer token. **Post-MVP:** refresh-token rotation with reuse detection per the OAuth 2.0 Security BCP (RFC 9700).
- **Validation:** signature and expiry verified on every protected request; the algorithm allowlist is pinned to HS256 (rejects the `none` algorithm and algorithm-confusion). Any invalid/expired/tampered token yields a single generic `401` (no reason leaked). Login failures are likewise generic and timing-equalized (no user enumeration).

## Authorization (RBAC)
Roles → permissions matrix, enforced via middleware/dependencies.

| Role | Articles: read | Articles: create | Articles: update/delete | Admin ops |
|---|---|---|---|---|
| user | ✅ | ✅ (own) | ✅ (own) | ❌ |
| admin | ✅ | ✅ | ✅ (any) | ✅ |

## Enforcement Points
- _Where authz is checked (FastAPI dependency, middleware) and how ownership is verified._

## Abuse Cases to Test
- Auth bypass attempts, token tampering, privilege escalation, IDOR on `{id}` routes.
- See [threat-model.md](threat-model.md) and [owasp-matrix.md](owasp-matrix.md).
