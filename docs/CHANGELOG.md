# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Added
- Walking skeleton: `create_app()` factory, lazy async DB/Redis clients, and `/health/live` +
  `/health/ready` Kubernetes probe endpoints.
- Article CRUD endpoints (FR-004) under `/v1/articles` with the canonical error envelope
  (including `request_id`), Pydantic input bounds, and soft-delete.
- Keyset pagination and `author` filter for the article list (FR-005): opaque base64url cursor,
  `limit` 1–100 (default 20), newest-first with `id` tiebreaker, soft-deleted rows excluded.
- Optimistic concurrency on writes: `If-Match` precondition with single-statement compare-and-set
  (missing → 422, stale → 409), enforced under a transaction-per-request boundary.
- JWT authentication (FR-001/FR-002): `POST /v1/auth/register` (Argon2id password hashing, default
  `user` role, 201 with no password echoed, duplicate email -> 409) and `POST /v1/auth/login` (HS256
  access token with `sub`/`role`/`iat`/`exp`, 15-min TTL, generic timing-equalized 401, no user
  enumeration). Token verification runs in an `AuthMiddleware` that attaches the caller's principal;
  `get_current_user` resolves it to the live `users` row. Replaces the temporary `X-User-Id` dev stub.
- JWT signing configured from the environment: `JWT_SECRET` (held as `SecretStr`, kept out of
  repr/logs), `JWT_ALGORITHM` (default HS256), `JWT_EXPIRE_SECONDS`; the app fails closed if the 
  signing key is unset rather than signing with an empty key.
- Alembic migrations `0001`–`0004`: schema (roles/users/articles), role seeds, and the two composite
  list indexes (`idx_articles_deleted_created`, `idx_articles_author_deleted_created`).
- Test harness and suite (acceptance/unit/integration) with an 80% coverage floor enforced via
  `pyproject.toml`.
- Project documentation scaffold (`docs/` tree).
- Repository community files and MkDocs configuration.
- Data model / ERD (`docs/data/data-model.md`) and schema & migrations guide (`docs/data/migrations.md`).
- ADR-0007 (single role per user via FK) and ADR-0008 (Alembic for schema migrations).
- `cryptography` runtime dependency — required by aiomysql/pymysql for MySQL 8 `caching_sha2_password` authentication.

[Unreleased]: https://github.com/si-sibomana/scalable-content-platform/commits/main
