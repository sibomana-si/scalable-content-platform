# OpenAPI Specification

> **Status:** 🟥 Draft

FastAPI auto-generates an OpenAPI schema. Treat it as the API contract.

## Live endpoints (when app is running)
- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- Raw schema: `GET /openapi.json`

## Committed snapshot
Export and version a snapshot so contract changes show up in diffs:

```bash
# once the app exists, e.g.:
python -c "import json, app.main as m; print(json.dumps(m.app.openapi()))" > docs/api/openapi.json
```

Store the exported file as `docs/api/openapi.json` and update it on every contract change. Consider failing CI when it drifts from the running app.
