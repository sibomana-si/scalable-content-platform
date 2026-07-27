# API Reference / Usage Guide

> **Status:** 🟩 Current for the shipped `/v1/articles` API (auth endpoints planned) · Authoritative schema lives in [openapi.md](openapi.md).

## Base URL & Versioning
- Base: `/v1`
- See [versioning-policy.md](versioning-policy.md)

## Authentication Flow

**Current (dev stub):** there are no auth endpoints yet. Authenticated routes identify the caller
from a dev-only `X-User-Id` header that must resolve to a real `users` row; a missing or unknown id
returns `401 UNAUTHENTICATED`. Ownership/admin authorization on top of it is real and enforced.

```bash
curl -X POST $BASE/v1/articles -H 'X-User-Id: 1' \
  -d '{"title":"...","body":"..."}' -H 'Content-Type: application/json'
```

**Planned (FR-001/FR-002/FR-003):** register/login endpoints issuing a signed JWT access token, sent
as `Authorization: Bearer <token>`, with role-based authorization. The Week-3 work replaces only the
`get_current_user` dependency body — the routes and authorization rules above are unchanged.

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
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/v1/articles` | **public** | List (paginated) |
| GET | `/v1/articles/{id}` | **public** | Read one |
| POST | `/v1/articles` | user | Create (author = caller) |
| PUT | `/v1/articles/{id}` | owner/admin | Full replace; requires `If-Match` |
| DELETE | `/v1/articles/{id}` | owner/admin | Soft delete; requires `If-Match` |
| POST | `/v1/auth/register` | none | Register — _planned (FR-001)_ |
| POST | `/v1/auth/login` | none | Login — _planned (FR-002)_ |

Reads are public per FR-005 — anonymous clients can list and fetch live articles (an
earlier draft of this table wrongly required `user` auth on reads). "user" auth is the dev
`X-User-Id` stub today (see Authentication Flow).

**Optimistic concurrency (writes):** `PUT`/`DELETE` require
`If-Match: <updated_at as returned in the article JSON>` (ISO-8601 with microseconds;
quotes tolerated). Missing → `422`; token no longer current → `409 CONFLICT` (re-fetch
and retry). See PRD D11.

_Errors: see [error-catalog.md](error-catalog.md)._
