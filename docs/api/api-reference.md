# API Reference / Usage Guide

> **Status:** 🟩 Current for the shipped `/v1/articles` and `/v1/auth` APIs · Authoritative schema lives in [openapi.md](openapi.md).

## Base URL & Versioning
- Base: `/v1`
- See [versioning-policy.md](versioning-policy.md)

## Authentication Flow

Register, then log in to obtain a short-lived JWT access token, and send it as 
`Authorization: Bearer <token>` on protected (write) routes. Tokens are **HS256**, carry 
`sub`/`role`/`iat`/`exp`, and expire after 15 minutes; there is no refresh token; re-login on 
expiry (FR-001/FR-002, [authn-authz](../security/authn-authz.md)).

```bash
# 1. Register (201; the password is never echoed back)
curl -X POST $BASE/v1/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"me@xample.com","password":"a-strong-passphrase"}'
  
# 2. Log in -> {"access_token":"<jwt>","token_type":"bearer"}
TOKEN=$(curl -sX POST $BASE/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"a-strong-passphrase"}' | jq -r .access_token)
  
# 3. Call a protected route with the bearer token
curl -X POST $BASE/v1/articles -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"title":"...","body":"..."}'
```

A missing token on a protected route -> `401 UNAUTHENTICATED`; an invalid/expired/tampered token -> 
`401`; a valid token whose role lacks permission -> `403 FORBIDDEN`. Invalid login credentials return a 
generic `401` (no user enumeration). Per-resource ownership (author-or-admin) is enforced in the 
service layer.

## Pagination & Filtering Conventions

List endpoints use **keyset (cursor) pagination** — never `page`/`OFFSET`
([indexing-strategy](../data/indexing-strategy.md), PRD D9). Ordering is fixed:
newest first by `created_at`, `id` as deterministic tiebreaker; there is no `sort` param.

| Param | Meaning | Default |
|---|---|---|
| `limit` | Page size, 1–100 (out of bounds → `422 VALIDATION_ERROR`) | 20 |
| `cursor` | Opaque continuation token from the previous response's `next_cursor`; treat as a black box (malformed/tampered → `422 VALIDATION_ERROR`) | — (first page) |
| `author` | Only articles by this author (user id ≥ 1); composes with `cursor`; unknown author → empty list | — (no filter) |

Responses are `{"items": [...], "next_cursor": "..."}`; `next_cursor` is `null` on the
last page. Cursors encode the last row's `(created_at, id)` position, so a walk never
duplicates or skips rows even as new articles are created ahead of the cursor.

## Endpoints (summary)
| Method | Path | Auth        | Description                              |
|---|---|-------------|------------------------------------------|
| GET | `/v1/articles` | **public**  | List (paginated)                         |
| GET | `/v1/articles/{id}` | **public**  | Read one                                 |
| POST | `/v1/articles` | user        | Create (author = caller)                 |
| PUT | `/v1/articles/{id}` | owner/admin | Full replace; requires `If-Match`        |
| DELETE | `/v1/articles/{id}` | owner/admin | Soft delete; requires `If-Match`         |
| POST | `/v1/auth/register` | public      | Register -> `201 {id, email, role}`      |
| POST | `/v1/auth/login` | public      | Login -> `200 {access_token, token_type}` |

Reads are public per FR-005 — anonymous clients can list and fetch live articles (an
earlier draft of this table wrongly required `user` auth on reads). "user" auth means a valid 
`Authorization: Bearer` JWT (see Authentication Flow).

**Optimistic concurrency (writes):** `PUT`/`DELETE` require
`If-Match: <updated_at as returned in the article JSON>` (ISO-8601 with microseconds;
quotes tolerated). Missing → `422`; token no longer current → `409 CONFLICT` (re-fetch
and retry). See PRD D11.

_Errors: see [error-catalog.md](error-catalog.md)._
