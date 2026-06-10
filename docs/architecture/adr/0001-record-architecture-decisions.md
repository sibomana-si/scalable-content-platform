# ADR-0001: Record architecture decisions

> **Status:** Accepted · **Date:** 2026-06-04

## Context
We need a lightweight, durable way to capture significant architectural decisions and the reasoning behind them, so future contributors (and reviewers) understand *why* the system is the way it is — not just *what* it is.

## Decision
We will use Architecture Decision Records (ADRs) in the [Nygard format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions.html), stored as Markdown in `docs/architecture/adr/`, numbered sequentially (`0001`, `0002`, …), starting from [template.md](template.md). ADRs are immutable once accepted; changes are made by adding a new ADR that supersedes the old one.

## Consequences
**Positive**
- Decision history is versioned alongside the code and reviewed in PRs.
- New contributors can ramp up on rationale quickly.

**Negative / costs accepted**
- Small ongoing discipline cost to write a record per significant decision.

## Alternatives Considered
| Option | Pros | Cons | Why rejected |
|---|---|---|---|
| Wiki / external doc | Easy editing | Drifts from code, not PR-reviewed | Not version-controlled with code |
| No formal record | Zero overhead | Rationale lost over time | Defeats the project's goal of demonstrating judgment |

---

### Suggested initial ADRs to write next
- **ADR-0002** — Modular monolith with service boundaries vs. true microservices
- **ADR-0003** — MySQL as source of truth (vs. PostgreSQL)
- **ADR-0004** — Redis cache-aside vs. write-through caching
- **ADR-0005** — JWT (stateless) auth vs. server-side sessions
- **ADR-0006** — Migration tooling (e.g., Alembic)
