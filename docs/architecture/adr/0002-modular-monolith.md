# ADR-0002: Modular monolith with service boundaries

> **Status:** Accepted · **Date:** 2026-06-10 · **Last updated:** 2026-07-10

## Context
The project brief's architecture sketch names "Auth Service" and "Content Service," which reads as
microservices. The actual NFRs the topology must serve are horizontal scalability (stateless
scale-out), read P95 < 200 ms, and graceful degradation — none of which require multiple deployables.
This is a small-team MVP; every additional deployable adds CI/CD pipelines,
service discovery, inter-service auth, network failure modes, and distributed tracing complexity.

## Decision
We will build **one stateless FastAPI deployable** containing clearly bounded **auth** and **content**
modules — a modular monolith. Module boundaries are enforced in code (separate packages, no reaching
across layers; modules interact through service interfaces, not each other's tables/internals), so the
seams remain clean split lines if scale or team structure ever demands extraction. Documentation says
"modular monolith with service boundaries" honestly rather than borrowing microservice vocabulary.

## Consequences
**Positive**
- Horizontal scaling is fully preserved: the unit is stateless, so replicas scale out identically to microservices ([NFR](../../requirements/non-functional-requirements.md)).
- One pipeline, one image, one deployment to operate, observe, and secure — matched to MVP scope.
- In-process calls between auth and content: no inter-service network hops on the hot read path.

**Negative / costs accepted**
- Coarser deploy unit: any change ships the whole app; no independent release cadence per module.
- Shared failure domain for code defects (a crash bug in one module takes down the replica).
- Discipline required to keep module boundaries from eroding (enforced in code review).

**Neutral / follow-ups**
- Extraction trigger: split a module out only when an empirical limit appears (divergent scaling
  profiles, team ownership boundaries, independent release pressure) — not pre-emptively.

## Alternatives Considered
| Option | Pros | Cons | Why rejected |
|---|---|---|---|
| True microservices (auth + content as separate deployables) | Independent deploys/scaling; team autonomy at scale | Per-service CI/CD, discovery, inter-service authN, network failure modes, ops burden | Costs are real on day one; benefits only materialize at a team/scale size the MVP doesn't have |
| Unstructured monolith (no enforced module boundaries) | Fastest to start | Boundaries erode; future extraction becomes a rewrite | Forfeits the cheap option value that module seams preserve |
