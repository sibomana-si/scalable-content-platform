# OpenAPI Specification

> **Status:** 🟩 Current. The committed snapshot is [`openapi.json`](openapi.json), and CI fails
> when it drifts from the app.

FastAPI auto-generates an OpenAPI schema. Treat it as the API contract.

## Live endpoints (when app is running)
- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- Raw schema: `GET /openapi.json`

## Committed snapshot
[`openapi.json`](openapi.json) is the schema the app serves, committed so that contract changes
show up in diffs. It excludes `/metrics`, which is a scrape target, not part of the API.

Regenerate it after any change to a route, a request or response model, or the app version:

```bash
python scripts/export_openapi.py
```

Review the diff of `docs/api/openapi.json`, and commit it with the change that caused it. The
script writes sorted keys and two-space indentation, so the file is the same on every machine.
Do not edit the file by hand.

## Drift check
Two checks compare the committed file with the app schema, byte for byte:

- The **OpenAPI snapshot** step in the `lint` job of `.github/workflows/ci.yml` runs
  `python scripts/export_openapi.py --check`. It prints a unified diff and exits 1 on any
  difference.
- `tests/smoke/test_openapi_snapshot.py` runs the same comparison in `pytest -q`, so drift fails
  on your machine before the push.

To fix a red check, run `python scripts/export_openapi.py`, review the diff, and commit. A new
FastAPI or Pydantic release can also change the generated schema with no change to the code. The
schema that the app serves did change, so regenerate and commit it the same way.
