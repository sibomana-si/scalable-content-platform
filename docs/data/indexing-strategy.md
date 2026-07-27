# Indexing & Query Strategy

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-18

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

Captured 2026-07-18 on MySQL 8 against the **production list SQL** — the compiled output
of `build_list_query()` in `app/repositories/article_repo.py` — with ~500 articles across
5 authors, ~10% soft-deleted, spread `created_at`, after `ANALYZE TABLE articles`.
Regression-guarded by `tests/integration/test_article_indexes.py`, which re-runs
`EXPLAIN FORMAT=JSON` over the same compiled statements and asserts index choice and the
absence of filesort on every CI run (so this section can't silently rot).

**Before** (schema at migration `0002` — PK/FK indexes only): every list variant is a full
table scan plus filesort.

```
-- public list (first page): SELECT ... WHERE deleted_at IS NULL
--                           ORDER BY created_at DESC, id DESC LIMIT 21
-> Limit: 21 row(s)
    -> Sort row IDs: articles.created_at DESC, articles.id DESC, limit input to 21 row(s) per chunk
        -> Filter: (articles.deleted_at is null)
            -> Table scan on articles  (cost=51.2 rows=500)
-- JSON: access_type=ALL, key=null, rows_examined_per_scan=500, using_filesort=true
-- (continuation + author-filter variants: identical shape — table scan + filesort)
```

**After** (migrations `0003` + `0004`): each variant seeks its composite index and reads
the rows already in keyset order (reverse index scan — no filesort, no full scan).

```
-- public list (first page) → idx_articles_deleted_created
-> Limit: 21 row(s)
    -> Filter: (articles.deleted_at is null)
        -> Index lookup on articles using idx_articles_deleted_created (deleted_at=NULL) (reverse)
-- JSON: access_type=ref, rows_examined_per_scan=450, using_filesort=false

-- public list (cursor continuation) → idx_articles_deleted_created
-> Limit: 21 row(s)
    -> Index range scan on articles using idx_articles_deleted_created over
       (deleted_at = NULL AND created_at < '...') OR
       (deleted_at = NULL AND created_at = '...' AND id < 250) (reverse)
-- JSON: access_type=range, rows_examined_per_scan=226, using_filesort=false

-- author filter (first page) → idx_articles_author_deleted_created
-> Limit: 21 row(s)
    -> Filter: (articles.deleted_at is null)
        -> Index lookup on articles using idx_articles_author_deleted_created
           (author_id=2, deleted_at=NULL) (reverse)
-- JSON: access_type=ref, rows_examined_per_scan=50, using_filesort=false

-- author filter (cursor continuation) → idx_articles_author_deleted_created
-> Limit: 21 row(s)
    -> Index range scan on articles using idx_articles_author_deleted_created over
       (author_id = 2 AND deleted_at = NULL AND created_at < '...') OR
       (author_id = 2 AND deleted_at = NULL AND created_at = '...' AND id < 250) (reverse)
-- JSON: access_type=range, rows_examined_per_scan=26, using_filesort=false
```

Notes:

- The keyset predicate `(created_at < :c) OR (created_at = :c AND id < :i)` is handled as
  a two-branch reverse **range scan on one index** — InnoDB secondary indexes implicitly
  end with the PK, which is what makes the `id` tiebreaker part of the index order.
- The optimizer only prefers `idx_articles_author_deleted_created` when the author
  predicate is selective (as in production); with one author owning ~100% of rows it
  correctly falls back to `idx_articles_deleted_created`, which already provides the sort
  order. The regression test seeds 5 authors to mirror the selective case.
- InnoDB FK side effect: `idx_articles_author_deleted_created` became the supporting
  index for `fk_articles_author_id` (MySQL silently dropped the implicit FK index), so
  migration `0004`'s downgrade recreates the FK to restore the implicit index — see the
  revision docstring.

## Anti-patterns avoided
- Over-normalization 
- Unbounded `OFFSET` pagination on large tables (prefer keyset/cursor)
