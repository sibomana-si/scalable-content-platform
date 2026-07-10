# ADR-0007: Single role per user via FK, not a join table

> **Status:** Accepted · **Date:** 2026-06-11

## Context
RBAC defines exactly two roles, `user` and `admin`, with strictly increasing privilege
([PRD §5](../../requirements/product-requirements.md), FR-003). Every authenticated request relies
on a role claim embedded in the JWT at login ([ADR-0005](0005-stateless-jwt-auth.md)), and the
[data model](../../data/data-model.md) must represent the user↔role relationship somehow. The
classic options are a many-to-many join table (`user_roles`), a single FK on `users`, or an
inline enum/string column. The draft ERD left this open with a TODO to decide and justify.

## Decision
`users.role_id` is a **single NOT NULL foreign key** to a seeded `roles` lookup table — **exactly
one role per principal**. The default role `user` is assigned explicitly by the application at
registration (FR-001), not by a column default. Roles remain a *table* (not an enum) so the role
set is data: seeded idempotently by a migration ([migrations §8](../../data/migrations.md)),
extensible without DDL, and FK-protected against typos and orphans.

## Consequences
**Positive**
- The login path resolves a user's role with one indexed join (or none, once the role name is
  denormalized into the JWT claim); no per-request role aggregation.
- The JWT role claim is unambiguous — with a join table, "which of the user's roles goes in the
  token?" needs an answer; with one FK the question cannot arise.
- Admin promote/demote is a single audited `UPDATE` (the audit-logging requirement in
  [NFR Security](../../requirements/non-functional-requirements.md) covers role changes).
- Referential integrity is enforced by the database (`fk_users_role_id`, `ON DELETE RESTRICT`):
  a role with members cannot vanish, and an invalid role cannot be assigned.

**Negative / costs accepted**
- Multi-role composition (a user who is both editor and auditor, say) requires a schema change.
  The path is documented and non-breaking: introduce `user_roles` via expand/contract, backfill
  one row per user from `role_id`, switch reads, drop the column
  ([data-model §4.2](../../data/data-model.md)).
- Fine-grained permissions (role→permission mapping) are likewise deferred; with two roles,
  permission checks are simple role comparisons in middleware.

**Neutral / follow-ups**
- If a third role ever lands, this design holds (seed another row). It is *composition* —
  multiple roles per user — that triggers the join-table migration, not role count.

## Alternatives Considered
| Option | Pros | Cons | Why rejected |
|---|---|---|---|
| `user_roles` join table | Standard many-to-many; supports composition | Extra join/query on the auth path; forces a multi-role JWT-claim design the product doesn't have; more invariants to test | No current or planned requirement for more than one role per user — paying the complexity now buys nothing ([PRD §13 D5](../../requirements/product-requirements.md) shows the same instinct: avoid authz edge cases without a user need) |
| `ENUM('user','admin')` column | No join ever; one fewer table | Adding a role is DDL on the (large) `users` table; MySQL enum reordering pitfalls; role metadata has nowhere to live | Role set becomes schema instead of data; DDL-per-role-change is the wrong coupling |
| Plain string column | Simplest possible | No integrity — typos create phantom roles; no FK, no lookup row to hang metadata on | Gives up database-enforced integrity for no gain over a 1-byte FK |
| Role→permission tables (full RBAC schema) | Maximum flexibility | Three+ tables and a permission-resolution layer for a two-role system | Over-engineering at MVP scope; middleware role checks satisfy FR-003 |
