# API Reference / Usage Guide

> **Status:** 🟥 Draft · Authoritative schema lives in [openapi.md](openapi.md).

## Base URL & Versioning
- Base: `/v1`
- See [versioning-policy.md](versioning-policy.md)

## Authentication Flow
1. `POST /v1/auth/register` — create account
2. `POST /v1/auth/login` — returns access token
3. Send `Authorization: Bearer <token>` on protected routes

```bash
curl -X POST $BASE/v1/auth/login -d '{"email":"...","password":"..."}' -H 'Content-Type: application/json'
```

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
| POST | `/v1/auth/register` | none | Register |
| POST | `/v1/auth/login` | none | Login |
| GET | `/v1/articles` | **public** | List (paginated) |
| GET | `/v1/articles/{id}` | **public** | Read one |
| POST | `/v1/articles` | user | Create (author = caller) |
| PUT | `/v1/articles/{id}` | owner/admin | Full replace; requires `If-Match` |
| DELETE | `/v1/articles/{id}` | owner/admin | Soft delete; requires `If-Match` |

Reads are public per FR-005 — anonymous clients can list and fetch live articles (an
earlier draft of this table wrongly required `user` auth on reads).

**Optimistic concurrency (writes):** `PUT`/`DELETE` require
`If-Match: <updated_at as returned in the article JSON>` (ISO-8601 with microseconds;
quotes tolerated). Missing → `422`; token no longer current → `409 CONFLICT` (re-fetch
and retry). See PRD D11.

_Errors: see [error-catalog.md](error-catalog.md)._
