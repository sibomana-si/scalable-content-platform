# Configuration Reference

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-09-01

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
| `DB_POOL_SIZE` | Connections kept open per replica | `10` | No |
| `DB_MAX_OVERFLOW` | Extra connections allowed above the pool size under load. The capacity model's replica ceiling assumes 10/5 | `5` | No |
| `DB_POOL_TIMEOUT` | Seconds a request waits for a connection before it fails. Never unbounded: an open wait turns one slow query into a total stall | `10.0` | No |
| `DB_POOL_RECYCLE` | Seconds before an idle connection is replaced. Proxies drop idle connections long before MySQL's `wait_timeout` (28800s), and a recycled connection avoids "server has gone away" | `1800` | No |
| `DB_CONNECT_TIMEOUT_SECONDS` | Seconds to open a connection. aiomysql defaults to no connect timeout, so a blackholed host holds the request until the kernel gives up | `5.0` | No |
| `DB_STATEMENT_TIMEOUT_SECONDS` | Server-side statement ceiling, sent as MySQL `max_execution_time` on every connection. A client that gives up alone leaves the query running and the connection pinned to it | `2.0` | No |
| `DB_CALL_TIMEOUT_SECONDS` | Client-side ceiling over the whole guarded call, retries included. Must exceed `DB_STATEMENT_TIMEOUT_SECONDS`, or the client abandons the query before the server kills it | `3.0` | No |
| `DB_RETRY_ATTEMPTS` | Attempts, not retries: 2 means one retry. Reads only. A retried write is a second write, and nothing downstream can tell the two apart | `2` | No |
| `RETRY_BACKOFF_BASE_SECONDS` | Base of the exponential backoff. Each wait is drawn from `[0, min(base * 2**n, cap)]`; a fixed wait re-synchronizes every replica onto the same instant | `0.05` | No |
| `RETRY_BACKOFF_MAX_SECONDS` | Cap on one wait. Must not sit below the base, or the cap silently replaces it | `0.5` | No |
| `BREAKER_FAILURE_THRESHOLD` | Consecutive failures that open the circuit. Low enough to stop paying the timeout early, high enough that one slow query is not an outage | `5` | No |
| `BREAKER_RESET_SECONDS` | Seconds an open circuit fails fast before it admits a probe. Also the `Retry-After` value the caller receives | `10.0` | No |
| `BREAKER_HALF_OPEN_MAX_CALLS` | Probes admitted while half-open. More than one turns a recovery into a thundering herd against the dependency that just came back | `1` | No |
| `MAX_INFLIGHT_REQUESTS` | Requests one instance handles at once before it refuses the next with a 503. Derived from the M6 knee by Little's law, not chosen: 450 req/s × the 0.2 s read SLO = 90. Re-derive it for any other machine ([ADR-0012](../architecture/adr/0012-timeout-retry-and-circuit-breaker-policy.md)) | `90` | No |
| `SHED_RETRY_AFTER_SECONDS` | What a shed caller is told to wait, in seconds. Long enough for the burst to drain, short enough that a client does not read it as an outage | `1.0` | No |
| `READINESS_TIMEOUT_SECONDS` | Per-dependency ceiling for `/health/ready`. Kubernetes' probe `timeoutSeconds` should be ≥ this | `2.0` | No |
| `REDIS_URL` | Redis connection URL | — | Yes |
| `REDIS_SOCKET_TIMEOUT` | Per-command socket timeout (seconds). The library default is unbounded, which turns a blackholed Redis into an indefinite wait for every caller | `2.0` | No |
| `REDIS_SOCKET_CONNECT_TIMEOUT` | Connect timeout (seconds), same reasoning | `2.0` | No |
| `REDIS_MAX_CONNECTIONS` | Pool ceiling. redis-py grows its pool without limit, so a stalled server would open a socket per waiting caller | `50` | No |
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
