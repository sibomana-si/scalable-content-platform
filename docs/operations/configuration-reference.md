# Configuration Reference

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-08-14

Every environment variable: name, purpose, default, required. Mirror in `.env.example`.

| Variable | Purpose | Default | Required |
|---|---|---|---|
| `APP_ENV` | Environment (`dev`/`prod`) | `dev` | No |
| `APP_PORT` | HTTP listen port | `8000` | No |
| `LOG_LEVEL` | Logging verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`); an unrecognised value falls back to `INFO` | `INFO` | No |
| `LOG_FORMAT` | Log renderer: `json` (shipped) or `console` (human-readable, local dev only) | `json` | No |
| `MYSQL_HOST` | DB host | — | Yes |
| `MYSQL_PORT` | DB port | `3306` | No |
| `MYSQL_DB` | Database name | — | Yes |
| `MYSQL_USER` | DB user | — | Yes |
| `MYSQL_PASSWORD` | DB password (secret) | — | Yes |
| `DB_POOL_SIZE` | Connection pool size | `10` | No |
| `READINESS_TIMEOUT_SECONDS` | Per-dependency ceiling for `/health/ready`. Kubernetes' probe `timeoutSeconds` should be ≥ this | `2.0` | No |
| `REDIS_URL` | Redis connection URL | — | Yes |
| `REDIS_SOCKET_TIMEOUT` | Per-command socket timeout (seconds). The library default is unbounded, which turns a blackholed Redis into an indefinite wait for every caller | `2.0` | No |
| `REDIS_SOCKET_CONNECT_TIMEOUT` | Connect timeout (seconds), same reasoning | `2.0` | No |
| `CACHE_ENABLED` | Cache-aside master switch. `false` bypasses Redis completely — no connection, no keys — and every read goes to MySQL | `true` | No |
| `CACHE_ARTICLE_TTL_SECONDS` | TTL for a cached article body | `300` | No |
| `CACHE_LIST_TTL_SECONDS` | TTL for a cached list page. Shorter than an article TTL: pages go stale faster than rows | `60` | No |
| `CACHE_TTL_JITTER` | Fraction of the TTL to spread entries over. A fixed TTL expires a whole generation of keys at once and every reader then misses together | `0.2` | No |
| `CACHE_LOCK_TIMEOUT_SECONDS` | Single-flight lock TTL, and the budget a loser waits before it reads MySQL itself | `2.0` | No |
| `JWT_SECRET` | JWT signing key (secret): HS256 symmetric key; app refuses to sign/verify if unset | — | Yes |
| `JWT_ALGORITHM` | JWT signing algorithm | `HS256` | No |
| `JWT_EXPIRE_SECONDS` | Access token TTL (seconds) | `900` | No |
| `PASSWORD_HASH_MAX_THREADS` | Size of the dedicated Argon2id hashing pool. Caps concurrent hashes, so it is a memory ceiling (~64 MiB each) as much as a throughput one | `4` | No |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP/HTTP collector endpoint. **Unset disables tracing entirely** — no provider, no exporter, no outbound connections | — (tracing off) | No |
| `OTEL_SERVICE_NAME` | `service.name` on every span; without it a shared collector attributes traces to `unknown_service` | `scalable-content-platform` | No |

_Secrets (marked) are sourced per [secrets-management.md](../security/secrets-management.md) — never committed._
