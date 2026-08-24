# Testing Strategy & TDD Guide

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-08-24

How this project writes tests — and, more importantly, **when**: tests are written *before* the code
they verify. This document is the practical companion to two requirements that already exist:
the [NFR Maintainability](../requirements/non-functional-requirements.md) targets (coverage ≥ 80% on
core modules; unit/integration/contract/security tests in CI) and the acceptance criteria in the
[functional requirements](../requirements/functional-requirements.md). The decision to adopt TDD is
recorded in [ADR-0006](../architecture/adr/0006-test-driven-development.md).

## 1. The TDD policy

**A failing test precedes implementation.** The loop is the classic red → green → refactor:

1. **Red** — write the smallest test that expresses the next required behavior; run it; watch it fail
   for the *right reason* (assertion failure, not import error).
2. **Green** — write the minimum implementation that makes it pass.
3. **Refactor** — clean up with the test as the safety net; the test does not change unless the
   *requirement* changed.

**Definition of done includes the test that drove the change.** A PR that adds or changes behavior
must contain the test(s) that motivated it; reviewers should be able to check out the PR, revert the
implementation, and watch those tests fail.

## 2. Outside-in: the FRs are the test list

This project uses **double-loop ("outside-in") TDD**, anchored on the functional requirements:

- **Outer loop — acceptance tests.** Each `FR-xxx` acceptance criterion becomes a failing API-level
  test first (httpx `AsyncClient` against the FastAPI app), *before* any endpoint code exists. The
  FR documents are the canonical source of test cases — no invented behavior, no untested criteria.
- **Inner loop — unit tests.** While making an outer test pass, drive each service/logic component
  with fast unit tests (no I/O), red-green-refactoring at the function/class level.

**Naming convention for traceability:** acceptance tests carry the FR id in the module or test name,
e.g. `tests/acceptance/test_fr001_registration.py::test_rejects_password_under_12_chars`. This makes
requirements → test traceability greppable, and lets the eventual coverage report be read against the
FR list directly.

| FR | Acceptance-test module (convention) | Drives |
|---|---|---|
| FR-001 Registration | `tests/acceptance/test_fr001_registration.py` | Auth router + service, password policy (D6) |
| FR-002 Login (JWT) | `tests/acceptance/test_fr002_login.py` | Token issue, 15-min TTL (D1), no user enumeration |
| FR-003 RBAC | `tests/acceptance/test_fr003_rbac.py` | 401-before-403 ordering, public-read bypass |
| FR-004 Article CRUD | `tests/acceptance/test_articles.py` | Ownership, PUT full-replace (D10), optimistic concurrency `409` (D11), soft delete (D3) |
| Scale-out (in process) | `tests/integration/test_horizontal_scaling.py` | Two `create_app()` instances on one MySQL and one Redis: cross-instance reads, shared cache, invalidation across the process boundary, a JWT minted on one accepted by the other, no request affinity |
| Scale-out (real replicas) | `tests/integration/test_multi_replica.py` | The same properties across containers behind nginx. Marked `scale`; skipped unless `localhost:8080` answers |
| FR-004 Cache-aside reads | `tests/acceptance/test_fr004_cache.py` | Hit and miss counted on `/metrics`, cached body byte-identical, cursor round trip, read-your-writes after update and delete, a write by one author spares another author's page, a Redis failure during invalidation still returns 201 |
| FR-005 Paginated reads | `tests/acceptance/test_fr005_pagination.py` | Keyset cursor (D9), `author` filter (D7), bounded page size |
| FR-006 Retention purge | `tests/acceptance/test_fr006_purge.py` | Idempotency, retention-window boundary, untouched live rows |

## 3. Test pyramid, mapped to the architecture

The layers in the [component view (C4 L3)](../architecture/overview.md) each get the test type they
deserve — the layering exists precisely so each layer is independently testable:

| Layer (L3) | Test type | Speed / isolation | What it asserts |
|---|---|---|---|
| Routers + error envelope | **Contract** | Fast — app in-process, dependencies faked or containerized | Status codes, canonical [error envelope](../api/error-catalog.md), response schema vs. OpenAPI |
| Services (auth, article, admin) | **Unit** | Fastest — pure logic, no I/O | Ownership rules, password policy, token claims, invalidation decisions |
| Repositories | **Integration** | Real **MySQL** service container | SQL correctness, soft-delete filtering, keyset pagination edges (ties on `created_at`), transactions |
| Cache client | **Integration** | Real **Redis** service container | Cache-aside hit/miss/populate, TTL+jitter bounds, invalidation on write, fallthrough when Redis is down |
| JWT/RBAC middleware | **Security / abuse** | Fast — in-process | Negative cases are first-class: missing/expired/tampered token → `401`, wrong role → `403`, IDOR and ownership-bypass attempts rejected ([threat-model](../security/threat-model.md)) |
| Purge worker | **Integration** | Real MySQL | FR-006 idempotency and retention-window boundaries |

Integration tests run against the **same MySQL 8 / Redis 7 service containers CI already defines**
(`.github/workflows/ci.yml`); locally they use the docker compose stack, so
there is one set of tests, not a "CI suite" and a "local suite."

### Markers

| Marker | Needs | Runs in CI |
|---|---|---|
| _(none)_ | Nothing. Unit and smoke tests. | Yes |
| `integration` | MySQL and Redis, via `docker compose up -d` | Yes |
| `scale` | The app image and the compose scale profile | **No** |

Both marked suites skip on **port reachability**, not on a failed query: a local `pytest` with
no stack skips, while CI, which provisions the services, runs them and fails loudly if a
reachable dependency is misconfigured rather than masking it as a skip.

```bash
pytest -q                 # unit + smoke; the rest skip
pytest -m integration     # needs docker compose up -d
pytest -m scale           # needs docker compose --profile scale up -d --build --scale app=3
```

`scale` is excluded from CI because CI does not build the application image. Adding that build
is deployment work (M8). Until then the in-process statelessness suite
(`tests/integration/test_horizontal_scaling.py`) is what guards scale-out on every push, and
`tests/integration/test_multi_replica.py` is the local confirmation across real containers.

### Load tests

Load tests are not `pytest` tests. They live in `tests/load/` as k6 JavaScript, run in a container
behind the `load` compose profile, and sit outside `testpaths`, so `pytest` never collects them.
[ADR-0011](../architecture/adr/0011-k6-for-load-testing.md) records why.

| Path | What it is |
|---|---|
| `tests/load/lib/slo.json` | The NFR numbers in machine-readable form. The single source for every k6 threshold. |
| `tests/load/lib/` | The hot-set picker, the cursor walk, the payload builder, the API client, and the summary writer. |
| `tests/load/scenarios/` | `steady.js`, `ramp.js`, and `spike.js`. |
| `tests/load/selftest.js` | k6-native checks on `lib/`. Runs in about a second and needs no infrastructure. |

```bash
docker compose --profile load run --rm k6 run /scripts/selftest.js
docker compose --profile load run --rm k6 run /scripts/scenarios/steady.js
```

Run the selftest before every real scenario. A broken picker fakes the cache hit ratio, and every
latency number the run produced afterwards is fiction.

Four `pytest` tests guard the load suite from the Python side, because `ruff` does not lint
JavaScript:

| Test | Guards |
|---|---|
| `tests/unit/test_load_profile.py` | `slo.json` against the NFR table, and every scenario against the plan document. |
| `tests/unit/test_seed_dataset.py` | The seeder's batching, body bounds, and distribution shape. |
| `tests/unit/test_run_metadata.py` | The machine-state snapshot schema, and the rule that decides whether a run is citable. |
| `tests/unit/test_load_runbook.py` | Every script path, compose profile, k6 argument, and environment variable the runbook names. |

Two fixtures keep the shared services from leaking between tests. `clean_db` empties the tables
and disposes the engine; `clean_cache` deletes the application's Redis keys. Both are autouse in
the integration and acceptance suites, because a cached article from one test answering the next
test's read looks like a phantom rather than like leaked state.

### What is TDD'd — and what is not

- **Strictly test-first:** business logic, API contracts, authorization rules, cache invalidation,
  pagination — anything an FR acceptance criterion or PRD decision describes.
- **Verified, not TDD'd:** observability wiring (log fields, metric names, span presence) and infra
  glue are covered by integration/smoke assertions after wiring, since their "spec" is configuration.
  **M4 went further than this minimum, deliberately.** Wiring is still only verified, but every
  *decision* inside the observability layer was extracted into a pure function and driven
  red-green first: log redaction, request-id sanitising, route labelling, SQL-verb bucketing,
  tracing enablement, and the `traced()` no-op-without-a-provider contract. The rule of thumb this
  established: if a bug in it would be silent in production, it is a decision, not configuration,
  and it gets a unit test. Dashboards and alert rules remain configuration, but
  `tests/unit/test_dashboards.py` checks their PromQL against the metric names the app actually
  registers — the drift it guards against ("No data" panels, alerts that can never fire) is
  exactly the silent kind.
- **Validation, not TDD:** load tests (M6, [load-test-plan](../performance/load-test-plan.md)) and
  fault-injection/chaos runs (M7, [chaos-test-report](../resilience/chaos-test-report.md)) measure
  the running system against NFR targets; they are reports, not red-green loops. You cannot write
  a failing assertion for "P95 is 180 ms" before you have measured anything. **The rule applies to
  the scenario scripts, not to everything M6 ships.** The dataset seeder, the SLO-to-threshold
  consistency check, the replica scrape configuration, the hot-set picker, the run-metadata schema,
  and every optimization the measurement leads to were all written test-first. What was not: the
  three k6 scenarios, whose output *is* the assertion, and `scripts/perf_env.sh`, which is shell
  over `/sys` and is verified by running it.

## 4. Tooling & commands

| Concern | Choice |
|---|---|
| Runner | `pytest` + `pytest-asyncio` |
| API tests | `httpx.AsyncClient` against the FastAPI app (ASGI transport — no live server needed) |
| Integration deps | MySQL 8 / Redis 7 containers (CI services; docker compose locally) |
| Fixtures | Factory fixtures for users/roles/articles in `tests/conftest.py`; per-test DB isolation (transaction rollback or truncate) |
| Coverage | `pytest --cov` with `--cov-fail-under=80` ([NFR floor](../requirements/non-functional-requirements.md)) |
| Lint/format | `ruff check` + `ruff format --check` (already CI gates) |

Commands (canonical — also recorded in `CONTRIBUTING.md` at the repo root):

```bash
pytest -q                                  # full suite
pytest path/to/test_file.py::test_name     # a single test
pytest --cov --cov-fail-under=80           # with the coverage gate
```

Coverage is a **floor, not a goal** — the NFR wording applies: prioritize meaningful tests over the
number. A test that exists only to lift coverage and asserts nothing of value should be rejected in
review.

## 5. CI follow-ups (recorded here so they aren't forgotten)

Two changes to `.github/workflows/ci.yml` are due **when the first real test lands**
(not before — both would break a test-less repo):

1. ✅ **Done** (walking-skeleton smoke suite) — the pytest exit-code-5 tolerance (the "no tests
   collected → pass" shim in the `test` job) has been **removed**. Zero collected tests is now a
   failure, not a pass.
2. ✅ **Done** (FR-004/FR-005 slice) — the 80% floor is enforced via
   `[tool.coverage.report] fail_under = 80` in `pyproject.toml`, so any `pytest --cov=app` run
   (CI's included) gates without workflow changes. It landed with the Week-2 slice rather than
   FR-001 as originally sketched — the article CRUD/pagination suite was the first substantive
   behavior coverage.

## 6. References

- [ADR-0006 — Test-driven development](../architecture/adr/0006-test-driven-development.md) (the decision record)
- [Functional requirements](../requirements/functional-requirements.md) (the canonical test list)
- [NFR — Maintainability](../requirements/non-functional-requirements.md) (coverage floor, test types, CI gates)
- [Threat model](../security/threat-model.md) (source of security/abuse cases)
- [ADR-0011 — k6 for load testing](../architecture/adr/0011-k6-for-load-testing.md) (why the load suite is not Python)
- [Load test runbook](../performance/load-test-runbook.md) (how to repeat a measurement run)
- [Load test plan](../performance/load-test-plan.md) · [Chaos test report](../resilience/chaos-test-report.md) (validation, outside TDD)
