# ADR-0006: Test-driven development, outside-in from the functional requirements

> **Status:** Accepted · **Date:** 2026-06-10 · **Last updated:** 2026-07-10

## Context
The repository is pre-implementation: requirements are approved and testable (every FR carries
acceptance criteria; [PRD §13](../../requirements/product-requirements.md) D1–D12 fix the contested
behaviors), the [NFR](../../requirements/non-functional-requirements.md) already mandates ≥ 80%
coverage and four CI-run test types, and CI already provisions MySQL/Redis service containers for a
pytest job. What no document fixes is **when** tests are written. That ordering decision is cheapest
to make now — before the first line of application code — and it determines whether the NFR's
coverage floor is met by tests that *drove* the design or tests retrofitted to satisfy a number.
This is a process decision rather than a structural one, but it shapes the codebase (dependency
injection, layer seams, fake-ability) enough to deserve a record.

## Decision
We will practice **test-driven development for all application behavior**, in a double-loop,
outside-in form anchored on the functional requirements:

- **Outer loop:** each FR acceptance criterion becomes a failing API-level test (httpx against the
  FastAPI app) *before* the endpoint exists. The FR documents are the canonical test list; test
  names carry the FR id for traceability.
- **Inner loop:** red → green → refactor with unit tests on services and pure logic while making
  the outer test pass; repositories and the cache client are driven by integration tests against
  real MySQL/Redis containers (the same ones CI defines).
- **Scope boundary:** business logic, API contracts, authorization, caching, and pagination are
  strictly test-first. Observability wiring and infra glue are verified by integration/smoke checks;
  load (M6) and chaos (M7) testing remain validation activities, not TDD.

Definition of done includes the test that drove the change. Mechanics, naming, tooling, and the CI
follow-ups (drop the "no tests collected" tolerance; add the coverage gate) live in the
[testing strategy](../../development/testing-strategy.md).

## Consequences
**Positive**
- Requirements → test traceability comes free: the suite *is* the FR list, executable. Untestable or
  ambiguous criteria are exposed before implementation, not after.
- The NFR coverage floor is met as a by-product of the workflow rather than chased afterwards, and
  the tests assert intent (acceptance criteria) rather than implementation detail.
- Test-first pressure enforces the layering the architecture already promises ([overview §3](../overview.md)):
  injectable dependencies, services free of I/O, repositories behind seams — drift from that design
  becomes hard to write tests around, which surfaces it early.
- Security posture: negative cases (401/403 ordering, IDOR, ownership bypass) are written as
  first-class acceptance tests, satisfying the PRD's "negative tests" risk mitigation by default.

**Negative / costs accepted**
- Each feature starts slower: the first commit of any endpoint is a failing test, not working code.
  Accepted — the plan's pace is sustained by not revisiting broken behavior later.
- A real test-maintenance burden: refactors touch tests; acceptance criteria changes ripple into
  named test modules. Mitigated by testing contracts (status codes, envelopes, invariants) rather
  than internals.
- Over-mocking temptation in the inner loop can produce tests that pass against fakes and fail
  against reality. Mitigated structurally: repositories and the cache client are *only* tested
  against real containers, never mocks.
- Discipline cost: TDD degrades quietly if "test-after when busy" is tolerated. The PR rule
  (the test that drove the change ships with it) is the enforcement point.

**Neutral / follow-ups**
- The exit-code-5 tolerance in CI and the missing `--cov-fail-under=80` gate are intentional
  pre-scaffold states; both flip when the first real test lands (recorded in the testing strategy).
- If TDD strictness proves wrong for some component class, amend the scope boundary here (or
  supersede this ADR) — don't erode it silently.

## Alternatives Considered
| Option | Pros | Cons | Why rejected |
|---|---|---|---|
| Test-after (implement, then cover) | Faster first commit per feature | Tests inherit the implementation's blind spots; coverage becomes a retrofitting chore; design feedback arrives too late to act on | The project's stated purpose is demonstrating engineering discipline; test-after demonstrably yields weaker suites for the same coverage number |
| Coverage-only mandate (the NFR floor alone, no ordering rule) | Maximum flexibility | ≥ 80% says nothing about *when* or *what kind*; invites assertion-light tests written to satisfy the gate | Already the status quo — it leaves the highest-leverage decision (ordering) unmade |
| BDD with Gherkin tooling (behave/pytest-bdd) | Acceptance criteria stay readable by non-engineers | Extra translation layer and tooling for a single-developer project whose FRs are already written as testable criteria | The FR docs already serve as the readable spec; plain pytest with FR-named tests gets the traceability without the ceremony |
