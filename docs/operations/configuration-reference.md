# Configuration Reference

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-12

Every environment variable: name, purpose, default, required. Mirror in `.env.example`.

| Variable | Purpose | Default | Required |
|---|---|---|---|
| `APP_ENV` | Environment (`dev`/`prod`) | `dev` | No |
| `APP_PORT` | HTTP listen port | `8000` | No |
| `LOG_LEVEL` | Logging verbosity | `INFO` | No |
| `MYSQL_HOST` | DB host | — | Yes |
| `MYSQL_PORT` | DB port | `3306` | No |
| `MYSQL_DB` | Database name | — | Yes |
| `MYSQL_USER` | DB user | — | Yes |
| `MYSQL_PASSWORD` | DB password (secret) | — | Yes |
| `DB_POOL_SIZE` | Connection pool size | _TBD_ | No |
| `REDIS_URL` | Redis connection URL | — | Yes |
| `JWT_SECRET` | JWT signing key (secret) | — | Yes |
| `JWT_EXPIRE_SECONDS` | Access token TTL | _e.g. 900_ | No |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Trace exporter | — | No |

_Secrets (marked) are sourced per [secrets-management.md](../security/secrets-management.md) — never committed._
