"""The capacity model's connection arithmetic, made executable."""

from app.config import Settings

MYSQL_MAX_CONNECTIONS = 151  # MySQL 8 default
RESERVED = 20  # purge worker, migrations, operator sessions
MVP_REPLICAS = 4
DOCUMENTED_REPLICA_CEILING = 8


def per_replica_maximum(settings: Settings) -> int:
    return settings.db_pool_size + settings.db_max_overflow


def test_the_documented_pool_shape_is_what_the_code_uses():
    """The document says 10/5. If the defaults move, the document is wrong, not the code."""

    settings = Settings()

    assert (settings.db_pool_size, settings.db_max_overflow) == (10, 5)
    assert per_replica_maximum(settings) == 15


def test_the_mvp_replica_count_fits_under_the_connection_limit():
    settings = Settings()
    total = MVP_REPLICAS * per_replica_maximum(settings) + RESERVED

    assert total <= MYSQL_MAX_CONNECTIONS


def test_the_replica_ceiling_matches_the_documented_figure():
    settings = Settings()
    ceiling = (MYSQL_MAX_CONNECTIONS - RESERVED) // per_replica_maximum(settings)

    assert ceiling == DOCUMENTED_REPLICA_CEILING


def test_one_replica_past_the_ceiling_exhausts_the_limit():
    """The ceiling is real: the arithmetic must actually break above it."""

    settings = Settings()
    total = (DOCUMENTED_REPLICA_CEILING + 1) * per_replica_maximum(settings) + RESERVED

    assert total > MYSQL_MAX_CONNECTIONS
