# OpenAPI Specification

> **Status:** 🟨 App live — schema auto-generated; committed snapshot + CI drift-gate still TODO.

FastAPI auto-generates an OpenAPI schema. Treat it as the API contract.

## Live endpoints (when app is running)
- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- Raw schema: `GET /openapi.json`

## Committed snapshot
Export and version a snapshot so contract changes show up in diffs:

```bash
python -c "import json, app.main as m; print(json.dumps(m.app.openapi()))" > docs/api/openapi.json
```

**TODO (not yet wired):** commit the exported `docs/api/openapi.json` and add a CI check that fails
when it drifts from the running app. The snapshot is not committed today.
