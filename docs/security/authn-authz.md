# Authentication & Authorization

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-10

## Authentication (JWT)
- **Token type:** short-lived access token (JWT).
- **Claims:** _sub, role, exp, iat, jti — document the set._
- **Signing:** _algorithm (e.g. HS256/RS256) and key management; see [secrets-management.md](secrets-management.md)._
- **Expiry / refresh:** 15-minute access-token TTL; **no refresh token in MVP** (clients re-authenticate on expiry). Short TTL bounds the blast radius of a non-revocable bearer token. **Post-MVP:** refresh-token rotation with reuse detection per the OAuth 2.0 Security BCP (RFC 9700).
- **Validation:** signature, expiry, issuer/audience checked on every protected request.

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
