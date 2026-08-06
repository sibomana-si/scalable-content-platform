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

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Auth (JWT) ---
    # SecretStr keeps the signing key out of repr()/logs; read it via require_signing_key().
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 900

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
