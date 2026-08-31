"""Application settings"""

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Application ---
    app_env: str = "dev"
    app_port: int = 8000
    log_level: str = "INFO"
    log_format: str = "json"  # "json" for shipping, "console" for human-readable local dev

    # --- MySQL ---
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_db: str = "content_platform"
    mysql_user: str = "app"
    mysql_password: str = ""
    db_pool_size: int = 10
    # The capacity model's replica-ceiling arithmetic assumes 10/5 per replica:
    # (151 - 20 reserved) / 15 ~= 8 replicas.
    db_max_overflow: int = 5
    # Never unbounded. A pool wait with no ceiling is how one slow query becomes a total
    # stall: every later request queues behind it and nothing ever fails.
    db_pool_timeout: float = 10.0
    # Proxies and load balancers drop idle connections well before MySQL's wait_timeout
    # (28800s). A recycled connection avoids "server has gone away" on a request that did
    # nothing wrong.
    db_pool_recycle: int = 1800

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"
    # redis.asyncio defaults both of these to None. An unresponsive-but-reachable server then
    # hangs every command forever, and an unbounded wait is how a cache outage becomes a
    # caller outage: whatever the caller holds while waiting, it holds for good.
    redis_socket_timeout: float = 2.0
    redis_socket_connect_timeout: float = 2.0
    # redis-py grows its pool without limit. A stalled server would then have every waiting
    # caller open a new socket, so the cache outage becomes a file-descriptor outage.
    redis_max_connections: int = 50

    # --- Cache (cache-aside) ---
    # The cache is an optimization, never a dependency. Set CACHE_ENABLED=false and the read
    # path bypasses Redis completely — no connection, no keys — which is how the fall-through
    # path gets exercised without stopping the container.
    cache_enabled: bool = True
    cache_article_ttl_seconds: int = 300
    cache_list_ttl_seconds: int = 60
    # Fraction of the TTL to spread entries over. A fixed TTL expires a whole generation of
    # keys in one instant and every reader misses together.
    cache_ttl_jitter: float = 0.2
    # How long a single-flight lock is held, and how long a loser waits before it gives up and
    # reads the database itself. A slow loader must cost a duplicate query, never a hung request.
    cache_lock_timeout_seconds: float = 2.0

    # --- Auth (JWT) ---
    # SecretStr keeps the signing key out of repr()/logs; read it via require_signing_key().
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 900
    # Argon2id hashing runs in its own bounded thread pool. Each in-flight hash costs
    # ~64 MiB, so this is the memory ceiling as much as the throughput one: raise it for
    # login-heavy deployments with headroom, lower it under a tight limit.
    password_hash_max_threads: int = 4

    # --- Resilience ---
    # Every call to a dependency needs a ceiling at both ends. These are the ceilings; the
    # rules that keep them consistent live in `app/resilience/policy.py`, which refuses an
    # unbounded or self-contradictory set at start-up rather than under load.
    #
    # aiomysql opens a socket with no connect timeout by default, so a blackholed host holds
    # the request until the kernel gives up, which is minutes.
    db_connect_timeout_seconds: float = 5.0
    # The server-side kill, sent as `max_execution_time` on every new connection. A client that
    # gives up alone leaves MySQL running the query and the connection pinned to it.
    db_statement_timeout_seconds: float = 2.0
    # The client-side ceiling over the whole guarded call, retries included. It must exceed the
    # statement timeout, so the server kills a runaway query before the client abandons it.
    db_call_timeout_seconds: float = 3.0
    # Attempts, not retries: 2 means one retry. Reads only. A retried write is a second write,
    # and nothing downstream can tell the two apart.
    db_retry_attempts: int = 2
    # Full jitter between attempts: each wait is drawn from `[0, min(base * 2**n, max)]`. A
    # fixed wait re-synchronizes every replica onto the same instant, which is the stampede the
    # backoff exists to prevent.
    retry_backoff_base_seconds: float = 0.05
    retry_backoff_max_seconds: float = 0.5
    # Consecutive failures that open the circuit. Low enough to stop paying the timeout early,
    # high enough that one slow query is not an outage.
    breaker_failure_threshold: int = 5
    # How long an open circuit fails fast before it admits a probe.
    breaker_reset_seconds: float = 10.0
    # Probes admitted while half-open. More than one turns recovery into a thundering herd
    # against the dependency that just came back.
    breaker_half_open_max_calls: int = 1

    # --- Health probes ---
    # Per-dependency ceiling for /health/ready. Kubernetes' probe `timeoutSeconds` should be
    # at least this, or the orchestrator gives up while the handler is still working.
    readiness_timeout_seconds: float = 2.0

    # --- Observability ---
    # Empty endpoint = tracing off: no provider, no exporter, no outbound connection attempts.
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "scalable-content-platform"

    def require_signing_key(self) -> str:
        """Return the JWT signing key, or fail loudly if it is unset.

        Called at token-signing time so a misconfigured deployment refuses to mint
        tokens rather than silently signing with an empty key.
        """

        secret = self.jwt_secret.get_secret_value()
        if not secret:
            raise RuntimeError("JWT_SECRET is not configured; refusing to sign or verify tokens.")
        return secret

    def _database_url(self, driver: str) -> str:
        # Credentials are URL-encoded so passwords with reserved characters are safe.

        if self.mysql_password:
            auth = f"{quote_plus(self.mysql_user)}:{quote_plus(self.mysql_password)}"
        else:
            auth = quote_plus(self.mysql_user)
        return f"mysql+{driver}://{auth}@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}"

    @property
    def async_database_url(self) -> str:
        """SQLAlchemy async URL for the app (aiomysql): ``mysql+aiomysql://...``"""

        return self._database_url("aiomysql")

    @property
    def sync_database_url(self) -> str:
        """SQLAlchemy sync URL for Alembic migrations: ``mysql+pymysql://...``"""

        return self._database_url("pymysql")


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor: one resolved ``Settings`` per process."""

    return Settings()
