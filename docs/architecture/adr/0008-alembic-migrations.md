# ADR-0008: Alembic for schema migrations

> **Status:** Accepted · **Date:** 2026-06-11

## Context
The schema needs a versioned, reviewable change mechanism from day one. The
[NFR](../../requirements/non-functional-requirements.md) requires migrations to be *versioned,
reversible, and expand/contract* for zero-downtime deploys, and
[ADR-0003](0003-mysql-source-of-truth.md) accepted a cost that shapes the tooling requirement:
MySQL has **no transactional DDL**, so migrations must be small, individually reviewable steps —
the tool must make many small revisions cheap. The repositories are built on SQLAlchemy 2.x
models, and the data layer is tested only against real MySQL containers
([ADR-0006](0006-test-driven-development.md)), so whatever builds the test schema should be the
same thing that changes production. (ADR-0001 anticipated this decision when the ADR log was
created.)

## Decision
We will use **[Alembic](https://alembic.sqlalchemy.org/)**, with the SQLAlchemy model metadata as
the autogenerate source of truth. Operating rules — naming convention, comparison flags, the
sync-driver split (app async, migrations `mysql+pymysql://`), single-head linear history, CI
gates (clean upgrade, downgrade round-trip, `alembic check` drift detection), and deployment as a
pre-deploy Kubernetes Job — are specified in the
[schema & migrations guide](../../data/migrations.md), which is the operative companion to this
record. Integration tests build their schema by running the migration chain, not
`metadata.create_all()`, so every CI run exercises the same path production takes.

## Consequences
**Positive**
- Autogenerate diffs models against the live schema — drift between code and database becomes a
  CI failure (`alembic check`) instead of a production surprise.
- Offline rendering (`alembic upgrade --sql`) yields plain DDL for review, which is exactly the
  artifact MySQL's non-transactional DDL makes worth eyeballing before it half-applies.
- One bookkeeping table (`alembic_version`), one linear chain, and revision files reviewed in PRs
  — schema history is versioned alongside code, the same property ADR-0001 wanted for decisions.
- De-facto standard of the FastAPI/SQLAlchemy ecosystem: documentation, hiring familiarity, and
  tooling integrations come free.

**Negative / costs accepted**
- Autogenerate is a draft generator, not an authority — it misses or mangles MySQL specifics
  (`ON UPDATE CURRENT_TIMESTAMP(6)`, `MEDIUMTEXT`, charset/engine options). Mitigated by the
  mandatory review checklist in [migrations §4](../../data/migrations.md).
- Revision discipline is on us: parallel branches create multiple heads, which CI must reject;
  `downgrade()` quality must be enforced by the CI round-trip, or it rots.
- Migrations are Python code coupled to the app's model imports — fine here (single Python
  service), but it means migrations can't run without the codebase.

**Neutral / follow-ups**
- Alembic doesn't make DDL online or safe by itself; the `ALGORITHM=INSTANT/INPLACE` assertions
  and expand/contract sequencing in the migrations guide carry that. If a table ever outgrows
  in-place ALTER comfort, the next rung is an external online-schema-change tool
  (`gh-ost`/`pt-online-schema-change`) — orthogonal to this choice.

## Alternatives Considered
| Option | Pros | Cons | Why rejected |
|---|---|---|---|
| Raw SQL files + hand-rolled runner | Full control; DDL is exactly what's written | Re-implements versioning, ordering, and bookkeeping Alembic provides; no autogenerate; no model-drift detection | Building undifferentiated tooling instead of the product |
| Flyway / Liquibase | Mature, battle-tested, language-agnostic | JVM dependency in an otherwise pure-Python toolchain; no awareness of the SQLAlchemy models, so no diffing and double bookkeeping | The models already exist — a tool that can't see them gives up the drift gate |
| Declarative diff tools (skeema, Atlas) | Desired-state model is elegant; good MySQL support | Loses explicit per-change history and ordered data-migration steps; expand/contract *sequencing* is the hard part and diffing doesn't express it | Reviewable, ordered revisions are the point at MVP scale; revisit only if migration volume makes imperative files a burden |
| `metadata.create_all()` (no migrations) | Zero setup | No versioning, no evolution path, nothing for production change at all | Fails the NFR outright; acceptable only for throwaway scratch databases |
