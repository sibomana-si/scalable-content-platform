# Indexing & Query Strategy

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-12

Read-heavy workload — indexes and access patterns must be deliberate. Document the *why*, not just the *what*.

## Hot Access Patterns
| Query | Frequency | Supporting index |
|---|---|---|
| List live articles (paginated, by recency) | High | `(deleted_at, created_at)` |
| List a given author's live articles | Medium | `(author_id, deleted_at, created_at)` |
| Get article by id (live only) | High | PK + `deleted_at` predicate |
| Lookup user by email (login) | Medium | `UNIQUE(email)` |

## Index Inventory
| Table | Index | Columns | Type | Justification |
|---|---|---|---|---|
| articles | `idx_articles_deleted_created` | `(deleted_at, created_at)` | BTREE | Live-feed ordering with the soft-delete filter folded in (see below) |
| articles | `idx_articles_author_deleted_created` | `(author_id, deleted_at, created_at)` | BTREE | Author-filtered live feed — the only MVP list filter ([PRD §13 D7](../requirements/product-requirements.md)) |
| users | `uq_users_email` | `(email)` | UNIQUE | Auth lookup, dedupe |

## Soft delete & MySQL indexing
Articles use **soft delete** (`deleted_at` timestamp; `NULL` = live) per [PRD §13 D3](../requirements/product-requirements.md). Every read filters `WHERE deleted_at IS NULL`, so this predicate must be served by an index — otherwise the read-path P95 target degrades as deleted rows accumulate.

- **MySQL/InnoDB has no partial/filtered indexes** (unlike PostgreSQL's `... WHERE deleted_at IS NULL`). The filter therefore must be **folded into composite indexes** as a leading column — e.g. `(deleted_at, created_at)` lets the live-feed query seek to `deleted_at IS NULL` and then scan by recency.
- **A scheduled hard-purge** (PRD §13 D3 retention window) keeps the live set — and these indexes — lean over time, capping the cost of carrying soft-deleted rows.
- **Unique-constraint trap:** a soft-deleted row still occupies any `UNIQUE` slot (e.g. a slug). If articles gain a unique natural key, include `deleted_at` in that constraint or move purged rows to an archive table.

> Note: a `status` column (draft/published) is **out of scope for the MVP** ([PRD §13 D4](../requirements/product-requirements.md)); if a publish workflow is added later, revisit these indexes to lead with `(deleted_at, status, created_at)`.

## EXPLAIN Evidence
_Paste `EXPLAIN`/`EXPLAIN ANALYZE` output for hot queries before/after indexing._

```
-- EXPLAIN SELECT ... ;
```

## Anti-patterns avoided
- Over-normalization 
- Unbounded `OFFSET` pagination on large tables (prefer keyset/cursor)
