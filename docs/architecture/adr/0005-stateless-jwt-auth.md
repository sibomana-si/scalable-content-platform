# ADR-0005: Stateless JWT authentication (vs. server-side sessions)

> **Status:** Accepted · **Date:** 2026-06-10

## Context
Writes and admin actions require authentication and RBAC; reads are public. The API tier must stay
**stateless** so replicas can be added/removed freely with no sticky sessions
([NFR scalability](../../requirements/non-functional-requirements.md)). Server-side sessions would
reintroduce shared state — every authenticated request would consult a session store, putting that
store on the hot path and into the failure domain. The brief commits to "JWT + RBAC"; this ADR records
why that is also the right call, and how its main weakness is contained.

## Decision
We will authenticate with **stateless, signed JWT access tokens**: issued at login, **15-minute TTL,
no refresh token in MVP** (clients re-authenticate on expiry — [PRD §13 D1](../../requirements/product-requirements.md)).
Tokens carry the user id and role claim; **RBAC middleware** authorizes `user`/`admin` on 100% of
write/admin routes. Any replica validates any token by signature alone — no auth-time store lookup.
Signing keys live in the secret store ([secrets-management](../../security/secrets-management.md));
full flow in [authn-authz](../../security/authn-authz.md).

## Consequences
**Positive**
- Token validation is a local CPU operation on every replica — no session store on the hot path, no sticky sessions; horizontal scaling stays trivial.
- One fewer stateful dependency to operate, secure, and fail over.
- Role claim in the token makes RBAC middleware cheap and uniform.

**Negative / costs accepted**
- **No immediate revocation:** a stolen token cannot be invalidated server-side; D1 bounds this exposure to ≤ 15 minutes (aligned with the OAuth 2.0 Security BCP, RFC 9700).
- Role changes (e.g., admin demotion) take effect only at next token issuance — same ≤ 15-minute bound.
- Re-authentication every 15 minutes is a real UX cost, accepted for MVP in exchange for not building refresh infrastructure yet.
- Bearer-token discipline required: HTTPS only, no tokens in URLs or logs.

**Neutral / follow-ups**
- Post-MVP path, in order: **refresh-token rotation** (restores long sessions while keeping access tokens short), then a revocation denylist only if a concrete threat model demands it.

## Alternatives Considered
| Option | Pros | Cons | Why rejected |
|---|---|---|---|
| Server-side sessions (store in Redis/MySQL) | Instant revocation; small opaque cookies | Session lookup on every authed request; session store becomes hot-path, stateful, and availability-critical; sticky-session pressure | Re-couples the stateless tier to shared state — the property horizontal scaling depends on |
| Long-lived JWTs + denylist | Fewer logins | Denylist is a session store in disguise (lookup per request) with worse ergonomics | Combines the costs of both models |
| Opaque tokens + introspection endpoint | Centralized control | Introspection call per request = session lookup with extra latency | Same hot-path coupling, more moving parts |
