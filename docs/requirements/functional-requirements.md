# Functional Requirements

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-06-09

Each requirement uses a stable ID (`FR-xxx`) and includes acceptance criteria. These criteria reflect the
MVP product decisions recorded in [product-requirements.md](product-requirements.md) §13 (15-minute access
tokens with no refresh, soft delete, articles published on create, immutable authorship). Terms are defined
in the [Glossary](glossary.md). All error responses use the canonical envelope in
[api/error-catalog.md](../api/error-catalog.md); the HTTP codes below map to that catalog.

## FR-001 — User Registration
**Description:** A visitor can create a user account with a unique identifier and a password.
**Acceptance criteria:**
- [ ] Given valid, unique credentials, when a visitor registers, then an account is created with the default role `user` and a `201` is returned (the password is never echoed back).
- [ ] Given an identifier that is already registered, when registering, then the request is rejected (`409`) and no duplicate account is created.
- [ ] Given malformed input (missing fields, bad email/username format, password failing the policy, or any field exceeding its **maximum length**), then `422` with field-level errors and no account is created.
- [ ] **Password policy ([PRD §13 D6](product-requirements.md)):** minimum 12 characters; no mandatory composition or rotation rules; new passwords are screened against a known-breached-password list and rejected (`422`) on a match.
- [ ] Passwords are stored only as a salted hash — never in plaintext, never returned, never logged.

## FR-002 — User Login (JWT)
**Description:** A registered user authenticates and receives a short-lived JWT access token.
**Acceptance criteria:**
- [ ] Given valid credentials, when logging in, then a signed JWT access token with a **15-minute** expiry is returned.
- [ ] The token carries subject (user) and role claims used by authorization (FR-003).
- [ ] Given invalid credentials, then `401` with a generic message (no user-enumeration / no distinction between "unknown user" and "wrong password").
- [ ] No refresh token is issued in the MVP; on expiry the client re-authenticates.

## FR-003 — Role-Based Authorization
**Description:** `user` vs. `admin` permissions are enforced via middleware on protected routes.
**Acceptance criteria:**
- [ ] Public read endpoints (FR-005 / article `GET`) require no token.
- [ ] Given a missing, malformed, or expired token on a write/admin endpoint, then `401`.
- [ ] Given a valid token whose role lacks the required permission, then `403`.
- [ ] Admin-only actions (role management, modifying another user's article) reject non-admins with `403`.

## FR-004 — Article CRUD
**Description:** Authenticated users create, update, and (soft-)delete articles; reads are public. Authors manage their own articles; admins manage any.
**Acceptance criteria:**
- [ ] **Create:** an authenticated user creates an article owned by that user; it is **immediately public** (no draft/publish state in the MVP); returns `201`.
- [ ] **Read:** anyone, including anonymous clients, can fetch a non-deleted article by id; a soft-deleted or non-existent article returns `404`.
- [ ] **Update:** uses **`PUT` full-replace** of the article resource ([PRD §13 D10](product-requirements.md)); an author may update their own article; a non-owner non-admin receives `403`; an admin may update any article. Authorship cannot be changed.
- [ ] **Delete:** **soft delete** only — the article is marked deleted and excluded from reads, not physically removed; permitted to the author or an admin.
- [ ] **Optimistic concurrency ([PRD §13 D11](product-requirements.md)):** update/delete require an `updated_at`/version precondition (`If-Unmodified-Since`/`If-Match`); a stale write (the resource changed since the client read it) is rejected with `409` rather than silently overwriting.
- [ ] **Input bounds:** title and body are validated against **maximum lengths**; an oversized payload is rejected with `422` (also a DoS control — see [NFR Security](non-functional-requirements.md)).
- [ ] Article bodies are stored **raw** and returned as-is; the documented convention is **Markdown**, rendered client-side — the server does no rendering ([PRD §13 D8](product-requirements.md)).
- [ ] Every successful write **invalidates** the affected cache entries (see [caching-strategy](../data/caching-strategy.md)).

## FR-005 — Paginated / Filtered Reads
**Description:** Public list endpoints support pagination and filtering with stable ordering.
**Acceptance criteria:**
- [ ] List results exclude soft-deleted articles and require no authentication.
- [ ] Pagination uses **keyset/cursor** on `(created_at, id)` ([PRD §13 D9](product-requirements.md)), not `OFFSET`; the response returns an opaque cursor for the next page. Page size is bounded by an enforced default and maximum.
- [ ] Filtering by **`author`** is supported — the only filter field in the MVP ([PRD §13 D7](product-requirements.md)); ordering is deterministic and stable across pages (the `(created_at, id)` cursor guarantees no duplicates/skips between pages).
- [ ] List reads are served via cache-aside and meet the read-path P95 target ([NFR](non-functional-requirements.md)).

## FR-006 — Article Retention Purge (background)
**Description:** A scheduled background worker permanently removes articles that have been soft-deleted longer than the retention window ([PRD §13 D3](product-requirements.md)). Not an API endpoint — it runs off the request path.
**Acceptance criteria:**
- [ ] The job hard-deletes only articles whose `deleted_at` is older than the configured retention window; live and within-window articles are untouched.
- [ ] The job is **idempotent** — a re-run (or overlapping run) produces the same result and never double-acts or errors on already-purged rows.
- [ ] The job runs **off the request path**; its failure or slowness delays purging only and never affects reads/writes.
- [ ] Each run emits metrics and structured logs (rows scanned/purged, duration) and **alerts on failure** ([observability](../observability/observability-guide.md)).

---
_Add further requirements as the scope evolves. Cross-reference the [API Reference](../api/api-reference.md)._
