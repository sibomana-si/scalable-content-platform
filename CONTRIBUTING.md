# Contributing

Thanks for contributing to the Scalable Content Platform Backend.

## Local Development Setup

1. **Prerequisites:** Python 3.11+, Docker, Docker Compose. Local development currently uses
   **Python 3.14**; CI runs **3.11** — keep code compatible with 3.11.
2. **Create a virtual environment and install dependencies:**
   ```bash
   python3.14 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   pip install -r requirements-dev.txt   # runtime + test/lint/docs tooling
   ```
   `requirements-dev.txt` pulls in `requirements.txt` (runtime stack). For an exact, reproducible
   install pinned to a known-good resolution, use `pip install -r requirements.lock` instead
   (`requirements.lock` is resolved on 3.14). The `.venv/` directory is git-ignored — never commit it.
3. **Configure environment:**
   ```bash
   cp .env.example .env   # fill in secrets
   ```
4. **Start dependency services (MySQL 8 + Redis 7)** — needed for integration tests and for
   `/health/ready`. `docker-compose.yml` provides these (the app itself has no container image yet):
   ```bash
   docker compose up -d
   ```
5. **Apply database migrations** (schema, role seeds, indexes) to your local MySQL — integration
   tests migrate to head automatically, but running the app or Alembic by hand needs it:
   ```bash
   alembic upgrade head
   ```
6. **Run the app:**
   ```bash
   uvicorn app.main:app --reload   # http://127.0.0.1:8000  (/health/live, /health/ready, /v1/articles)
   ```
7. **Docs site (optional):** `mkdocs-material` is already in `requirements-dev.txt`:
   ```bash
   mkdocs serve   # http://127.0.0.1:8000
   ```

## Workflow
- Branch from `main`: `feat/<name>`, `fix/<name>`, `docs/<name>`.
- **TDD:** write the failing test first; a PR that adds or changes behavior must include the test
  that drove it (see the [testing strategy](docs/development/testing-strategy.md) and
  [ADR-0006](docs/architecture/adr/0006-test-driven-development.md)).
- Keep PRs focused; fill in the PR template.
- Significant decisions get an [ADR](docs/architecture/adr/template.md).

## Commit Convention
Use [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary`
(`feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`).

## Quality Gates
These match what CI (`.github/workflows/ci.yml`) enforces:
- Lint & format: `ruff check .` and `ruff format --check .`
- Tests: `pytest -q` — smoke tests pass with no dependencies; integration tests (`-m integration`)
  **skip** unless MySQL/Redis are reachable (`docker compose up -d`), and run in CI (which
  provisions both service containers).
- Run a single test: `pytest tests/smoke/test_health_live.py::test_live`
- Coverage: `pytest --cov=app`. The 80% floor is enforced via `[tool.coverage.report] fail_under = 80`
  in `pyproject.toml`, so any `pytest --cov=app` run (CI included) gates automatically — see the
  [testing strategy](docs/development/testing-strategy.md) §5.

## Documentation
Docs live in `docs/` and are reviewed in PRs (docs-as-code). Update relevant docs alongside code changes, and the [CHANGELOG](docs/CHANGELOG.md) under `[Unreleased]`.
