# ADR-0003: MySQL as the single source of truth

> **Status:** Accepted · **Date:** 2026-06-10

## Context
The system needs one durable, transactional store for `users`, `roles`, and `articles` — a small,
stable relational model with read-heavy access. The project brief commits the stack to MySQL
([PRD §9 constraints](../../requirements/product-requirements.md): "no substitutions"), so the open
architectural questions are not *which engine* but *what role the database plays* relative to the
cache, and whether the committed engine can carry the NFRs.

## Decision
We will use **MySQL (InnoDB) as the single source of truth**. Redis is strictly a cache: it may be
cold, stale within TTL bounds, or entirely absent without affecting correctness. All writes go to
MySQL transactionally; all invariants (ownership, roles, soft-delete state) live in MySQL. Schema
design follows the read-heavy brief: indexes shaped for the public read path and keyset pagination,
deliberate denormalization where it removes hot-path joins ([indexing-strategy](../../data/indexing-strategy.md)).

## Consequences
**Positive**
- A single, unambiguous consistency anchor — cache loss degrades latency, never data ([NFR durability](../../requirements/non-functional-requirements.md)).
- InnoDB row-level locking + transactions cover the brief's "transactional consistency" requirement, with optimistic concurrency ([PRD D11](../../requirements/product-requirements.md)) layered above.
- Mature operational story: backups + binlog give the PITR path behind the RPO ≤ 5 min / RTO ≤ 1 h targets.

**Negative / costs accepted**
- No transactional DDL: a failed migration can leave partial schema state — migrations must be small, reversible, and expand/contract ([migrations](../../data/migrations.md)).
- Fewer advanced types/features than PostgreSQL (rich JSON indexing, partial/expression index ergonomics); none are needed by the MVP model.
- Single-writer ceiling for write scaling — acceptable at ~5 writes/s ([capacity model](../capacity-scaling-model.md), B5).

**Neutral / follow-ups**
- Read scaling path when needed: read replicas behind the repository layer (reads dominate 100:1, so this is the natural first move).

## Alternatives Considered
| Option | Pros | Cons | Why rejected |
|---|---|---|---|
| PostgreSQL | Transactional DDL; richer types; arguably better default ergonomics | Functionally equivalent for this model; deviates from the committed stack | Brief commits MySQL; for a small relational CRUD model the NFRs are carried by schema/index/cache design, not engine choice — switching buys nothing the project measures |
| Document store (e.g., MongoDB) | Flexible schema | Relational model (users↔roles↔articles, FK ownership) is a natural fit; weaker fit for transactional RBAC writes | The data is relational; flexibility isn't a requirement |
