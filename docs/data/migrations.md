# Schema & Migrations Guide

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-12

How schema change works in this project: tooling, authoring rules, the MySQL-specific constraints
that shape them, zero-downtime patterns, CI gates, deployment, and rollback. The schema itself is
documented in [data-model.md](data-model.md); the durability targets these procedures must honor
(RPO ≤ 5 min, RTO ≤ 1 h, versioned + reversible + expand/contract migrations) are set in the
[NFR — Data Management & Durability](../requirements/non-functional-requirements.md).

## 1. Principles (the contract)

1. **Every schema change is a migration.** No manual DDL against any shared environment, ever —
   the migration chain is the only path from one schema version to the next, and the database's
   `alembic_version` must always identify a revision in the repo.
2. **One logical change per migration**, with a descriptive message. Small steps are the primary
   mitigation for MySQL's lack of transactional DDL (§5.1).
3. **Forward-only in production.** `downgrade()` is written wherever feasible (and exercised in
   CI, §9) but exists for development convenience and CI verification — production recovery is
   roll-forward, per the procedure in §12.
4. **Never edit an applied migration.** Once a revision has run anywhere beyond a local scratch
   database, fixing it means a new revision.
5. **Expand/contract for anything an old app version could notice.** Every migration must be
   compatible with both the app version being deployed *and* the one still running during the
   rollout (§6) — this is what makes zero-downtime deploys and instant app rollbacks possible.
6. **Schema migrations and data migrations are separate revisions** (§7). DDL steps stay fast and
   predictable; backfills are batched and idempotent.
7. **Linear history, single head.** Parallel branches are merged (rebased to a new `down_revision`)
   before merge to `main`; CI fails on multiple heads (§9).

## 2. Tooling

**[Alembic](https://alembic.sqlalchemy.org/)** with SQLAlchemy 2.x models as the autogenerate
source of truth. Why Alembic over the alternatives considered:

| Option | Why not |
|---|---|
| **Alembic** *(chosen)* | Native to the SQLAlchemy models the repositories use — autogenerate diffs models vs database; versioned, reversible revisions; offline `--sql` mode for reviewable DDL; the de-facto standard in the FastAPI/SQLAlchemy ecosystem |
| Raw SQL files + hand-rolled runner | Re-implements versioning, ordering, and bookkeeping Alembic already does; no autogenerate |
| Django-style / ORM-bundled migrations | Wrong ORM; the stack is committed ([PRD §9](../requirements/product-requirements.md)) |
| Schema-diff tools (e.g. skeema, atlas) | Declarative diffing is attractive but loses explicit, reviewable, per-change history and data-migration steps; revisit only if migration volume ever makes imperative files a burden |

Decision recorded in [ADR-0008](../architecture/adr/0008-alembic-migrations.md).

**Driver split:** the application uses an async MySQL driver; **migrations run synchronously**
(`mysql+pymysql://`) via a separate URL in Alembic's `env.py`. Migrations are batch jobs — async
buys nothing and complicates `env.py`; this is the standard pattern for async SQLAlchemy apps.

### Repository layout

```
alembic.ini                 # config: script location, file template
migrations/
  env.py                    # wires target_metadata, sync URL from env var, compare flags
  script.py.mako
  versions/
    20260615_a1b2c3d4e5f6_create_roles_users_articles.py
    20260615_b2c3d4e5f6a7_seed_roles.py
```

The migration database URL comes from the environment (12-factor,
[configuration-reference](../operations/configuration-reference.md)) — never hardcoded in
`alembic.ini`.

## 3. Configuration that makes autogenerate trustworthy

Three settings are load-bearing; without them autogenerate produces wrong or irreversible output.

**Naming convention** — set on the SQLAlchemy `MetaData` so every constraint gets a deterministic
name (matching the inventory in [data-model.md §5](data-model.md) and
[indexing-strategy](indexing-strategy.md)). Unnamed constraints get server-generated names that
differ per environment, which makes `downgrade()`/`DROP CONSTRAINT` unreliable:

```python
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "idx_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}
metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

**Comparison flags** — in `env.py`, so type and server-default drift is detected instead of
silently ignored:

```python
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    compare_type=True,
    compare_server_default=True,
)
```

**File template** — date-prefixed revision files so `versions/` sorts chronologically
(`alembic.ini`):

```ini
file_template = %%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s
```

## 4. Authoring workflow

```bash
# 1. Change the SQLAlchemy models (driven by a failing test, per ADR-0006 where applicable)
# 2. Generate a draft revision
alembic revision --autogenerate -m "add articles table"
# 3. REVIEW AND EDIT the draft — autogenerate output is a starting point, never final (checklist below)
# 4. Prove it round-trips locally against real MySQL 8 (the same container CI uses)
alembic upgrade head
alembic downgrade -1 && alembic upgrade head
# 5. Run the test suite; commit models + migration together in one PR
```

### Review checklist for every generated revision

- [ ] The diff contains **only** the intended change — autogenerate happily picks up unrelated
      model drift; remove anything that isn't this PR's logical change.
- [ ] **MySQL-isms autogenerate misses or mangles:** `mysql_engine="InnoDB"`,
      `mysql_charset/collate` on new tables; `MEDIUMTEXT` vs generic `Text`;
      `ON UPDATE CURRENT_TIMESTAMP(6)` server defaults (autogenerate cannot represent these —
      write them explicitly with `server_default=text(...)` / `server_onupdate`).
- [ ] Constraint and index names match the naming convention (no `_idx_1`-style server names).
- [ ] `downgrade()` is real and tested — not `pass` — or the revision documents *why* it is
      irreversible (e.g. a destructive contract step, which then requires the §12 PITR note).
- [ ] No schema + data mixed in one revision (§1.6).
- [ ] **Old-version compatibility:** would the currently deployed app still work mid-migration
      and after it? If not, split per expand/contract (§6).
- [ ] Locking impact assessed for the table size involved (§5.2).

## 5. MySQL-specific rules

### 5.1 No transactional DDL

InnoDB DDL **commits implicitly** — a failed multi-statement migration leaves the schema *partially
applied*, and Alembic's bookkeeping won't know how far it got
([ADR-0003](../architecture/adr/0003-mysql-source-of-truth.md), accepted cost). Therefore:

- Prefer **one DDL statement per revision**. Two tiny revisions beat one that can half-apply.
- Where multiple statements are unavoidable, order them so each prefix is a valid, harmless state,
  and make recovery trivial: re-running after a mid-failure should either no-op or be obviously
  fixable (e.g. create the index before the FK that needs it).
- Data migrations (DML) **do** run in transactions — batch-commit deliberately (§7) rather than
  relying on one giant transaction.

### 5.2 Online DDL — declare your lock expectations

MySQL 8 performs most DDL as `INSTANT` or `INPLACE`, but the failure mode of guessing wrong is a
table-level lock on the hot read path. For any migration touching `articles` (or any table once
it's large), **state the algorithm explicitly** so MySQL errors out instead of silently taking a
worse lock:

```python
op.execute(
    "ALTER TABLE articles ADD COLUMN slug VARCHAR(255) NULL, "
    "ALGORITHM=INSTANT"
)
# Falls back loudly: if INSTANT isn't possible, the statement FAILS instead of locking the table.
```

Rules of thumb (verify per operation against the MySQL online-DDL matrix):

| Operation | Expectation |
|---|---|
| `ADD COLUMN` (nullable, no default expression), `DROP COLUMN` | `INSTANT` |
| `ADD INDEX` / `DROP INDEX` | `INPLACE, LOCK=NONE` (CPU/IO cost, no write block) |
| Change column type, add `NOT NULL` to existing data | Table copy — **never directly on a large hot table**; use expand/contract (§6) |
| Add FK | `LOCK=NONE` with `foreign_key_checks` on; index must pre-exist |

At MVP volumes (~10k articles, [capacity model](../architecture/capacity-scaling-model.md)) any
of these completes in milliseconds — the rules exist so the discipline is already in place when
the table is 100× bigger. If a table ever outgrows in-place ALTER comfort, the documented next
rung is an external online-schema-change tool (`gh-ost` / `pt-online-schema-change`); out of MVP
scope.

### 5.3 Other MySQL traps

- **Renames are never one step.** `RENAME COLUMN`/`RENAME TABLE` breaks the still-running old app
  version instantly — always expand/contract (§6).
- **Unique constraints + soft delete:** adding any unique natural key to `articles` must account
  for soft-deleted rows still occupying the slot — see the trap documented in
  [indexing-strategy](indexing-strategy.md).
- **Charset consistency:** new tables/columns declare `utf8mb4` explicitly; never rely on server
  defaults that may differ between local, CI, and production.
- **`foreign_key_checks` stays ON** in migrations; ordering should make that possible (parents
  before children). Disabling it to force an ordering through is masking a bug.

## 6. Zero-downtime: the expand/contract pattern

Deploys are rolling ([NFR Operability](../requirements/non-functional-requirements.md) — graceful
shutdown, zero-downtime rollouts), so versions N and N+1 of the app run concurrently against one
schema. Every migration must be safe for **both**. The pattern:

| Phase | What | App compatibility |
|---|---|---|
| **1. Expand** | Add the new thing (nullable column, new table, new index) — purely additive | Old app ignores it; new app can't rely on it yet |
| **2. Migrate** | Backfill data in batches (§7); new app version writes both/reads new | Both versions correct |
| **3. Contract** | Remove the old thing (drop column/index, add `NOT NULL`) — only after no running version touches it | Ships at least one release **after** phase 2 is fully deployed |

**Worked example — adding a required `articles.slug` (hypothetical):**

1. *Expand (release k, revision 1):* `ADD COLUMN slug VARCHAR(255) NULL` (`INSTANT`).
2. *Migrate (release k, revision 2 + code):* app starts writing `slug` on create/update; a batched
   data migration backfills existing rows (§7).
3. *Verify:* `SELECT COUNT(*) FROM articles WHERE slug IS NULL` = 0; monitor a full release cycle.
4. *Contract (release k+1):* add the unique index, then `MODIFY ... NOT NULL` — by now no running
   code path can insert a NULL.

The same decomposition covers renames (add new → dual-write → backfill → switch reads → drop old)
and type changes (new column of the new type → backfill → swap). The discipline costs extra
revisions; it buys the ability to roll the **app** back at any moment without touching the schema
— which is precisely why production schema rollback (§12) is almost never needed.

## 7. Data migrations & backfills

- **Separate revision** from any DDL, named for what it does (`backfill_article_slugs`).
- **Batched**, committing every batch — never one transaction over a whole table (long
  transactions block purges, bloat undo, and stall replication if replicas arrive later):

```python
def upgrade() -> None:
    conn = op.get_bind()
    while True:
        result = conn.execute(sa.text(
            "UPDATE articles SET slug = ... "
            "WHERE slug IS NULL LIMIT 1000"
        ))
        conn.execute(sa.text("COMMIT"))
        if result.rowcount == 0:
            break
```

- **Idempotent**: the `WHERE` clause selects only unprocessed rows, so a crashed run re-runs
  safely — the same property required of the FR-006 purge worker.
- **Bounded impact**: batch size sized so each batch is milliseconds; run off-peak if the table is
  hot; emits progress logs.
- `downgrade()` for a backfill is usually a documented no-op (the expand phase's column drop
  removes the data) — say so explicitly in the revision docstring.

## 8. Seed / reference data

The `roles` rows (`user`, `admin` — [data-model.md §3.1](data-model.md)) are **seed data applied
as an idempotent data migration**, not application startup code (startup seeding races across
replicas) and not fixtures (production needs them too):

```python
def upgrade() -> None:
    op.execute(
        "INSERT INTO roles (name) VALUES ('user'), ('admin') "
        "ON DUPLICATE KEY UPDATE name = name"   # idempotent via uq_roles_name
    )

def downgrade() -> None:
    op.execute("DELETE FROM roles WHERE name IN ('user', 'admin')")
```

Tests rely on migrations (not `metadata.create_all()`) to build the schema in integration runs, so
seed data is identically present everywhere — and the migration chain itself is exercised on every
CI run as a by-product.

## 9. CI gates

Added to `.github/workflows/ci.yml` when the first models/migrations land (same trigger as the
coverage gate — see [testing strategy §5](../development/testing-strategy.md)). All run against
the **real MySQL 8 service container** already configured in CI, per the no-mocks rule for the
data layer ([ADR-0006](../architecture/adr/0006-test-driven-development.md)):

| Gate | Command | Catches |
|---|---|---|
| Single head | `test "$(alembic heads -q \| wc -l)" -eq 1` | Unmerged parallel migration branches |
| Clean upgrade | `alembic upgrade head` on an empty database | Broken chain, MySQL-incompatible DDL |
| Round-trip | `alembic downgrade base && alembic upgrade head` | Untested/fake `downgrade()` |
| Model–migration drift | `alembic check` (after upgrade) | Model edited without a migration |
| Reviewable SQL | `alembic upgrade head --sql > /dev/null` | Revisions that can't render offline; the output is also the artifact to eyeball when a diff looks suspicious |

The integration test suite then runs against the migrated schema, which is the strongest gate of
all: the schema the tests prove is exactly the schema migrations produce.

## 10. Deployment

- **Migrations run as a discrete pre-deploy step** — a Kubernetes `Job` that must succeed before
  the new app version rolls out (see [deployment](../operations/deployment.md)). **Not** an init
  container: with N replicas, N init containers race to migrate; a Job runs exactly once per
  release.
- The expand/contract rule (§6) means the running (old) version is always compatible with the
  just-migrated schema, so there is no ordering window where requests fail.
- The Job uses the dedicated migration connection budget reserved in the
  [capacity model](../architecture/capacity-scaling-model.md) connection math.
- App startup does **not** auto-migrate. Replicas verify they are at/compatible with `head` and
  fail readiness otherwise — surfacing "deployed app ahead of schema" as a crisp probe failure
  rather than runtime errors mid-traffic.

## 11. Command reference

```bash
alembic revision --autogenerate -m "msg"   # draft a new revision from model diff
alembic revision -m "msg"                  # empty revision (data migrations)
alembic upgrade head                       # apply all pending
alembic upgrade +1 / downgrade -1          # step one revision either way (dev only)
alembic downgrade base                     # tear down (dev/CI only)
alembic current                            # revision the DB is at
alembic history --verbose                  # chain with messages
alembic heads -q                           # open heads (CI asserts exactly 1)
alembic check                              # fail if models have drifted from migrations
alembic upgrade head --sql                 # render SQL without executing (review artifact)
alembic merge -m "merge heads" <rev1> <rev2>  # last resort; prefer rebasing the branch
```

## 12. Rollback procedure

**Default: roll forward.** The deploy story is built so the *app* can roll back freely (expand/
contract keeps old code compatible with the new schema); rolling back the *schema* in production
is the exceptional case.

Decision tree when a migration-related problem ships:

1. **App misbehaves, schema fine** → roll back the app deployment. No schema action — this is the
   payoff of §6, and it resolves the overwhelming majority of incidents.
2. **Migration is bad but additive** (unused column/index, failed expand step) → leave it or write
   a new **forward** revision that removes it. Never `alembic downgrade` in production for this —
   it rewinds `alembic_version` and now history disagrees with every other environment.
3. **Migration destroyed or corrupted data** (botched contract step, bad backfill) → data
   incident, not a migration command:
   - Write a forward revision restoring structures if applicable.
   - Restore lost data from backup: daily full + binlog replay to the point just before the
     migration ran — the **PITR path behind RPO ≤ 5 min / RTO ≤ 1 h**
     ([NFR — Data Management](../requirements/non-functional-requirements.md)). Restore to a side
     instance first and reconcile, rather than blanket-restoring the primary, when only a subset
     of rows is affected.
   - Invalidate or flush affected cache keys after restore — Redis may be serving values from the
     bad window ([caching-strategy](caching-strategy.md)).
4. **Mid-failure partial DDL** (§5.1) → inspect actual schema state vs the revision's statements,
   bring the schema to the revision's end state manually (one-time, documented in the incident
   record), stamp if needed, then add the safeguard that would have prevented it.

Every production rollback/restore event gets an incident entry in the
[runbook](../operations/runbook.md) and, where it changed approach, a note in this document.

**Backfill considerations during any rollback:** if a data migration ran between backup point and
incident, replaying binlogs replays the backfill too — account for it when reconciling, and prefer
re-running the (idempotent, §7) backfill over hand-fixing rows.

## 13. References

- [Data model / ERD](data-model.md) · [Indexing strategy](indexing-strategy.md) · [Caching strategy](caching-strategy.md)
- [NFR — Data Management & Durability](../requirements/non-functional-requirements.md) (RPO/RTO, expand/contract requirement) · [ADR-0003](../architecture/adr/0003-mysql-source-of-truth.md) (no transactional DDL) · [ADR-0006](../architecture/adr/0006-test-driven-development.md) (real-container testing)
- [Deployment guide](../operations/deployment.md) (migration Job) · [Runbook](../operations/runbook.md) · [Testing strategy](../development/testing-strategy.md)
- External: [Alembic docs](https://alembic.sqlalchemy.org/) · [MySQL 8 online DDL](https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-operations.html) · [Expand/contract (ParallelChange)](https://martinfowler.com/bliki/ParallelChange.html)
