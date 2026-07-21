"""Migration-chain integration tests.

Integration runs build the schema by running migrations, never metadata.create_all(),
so the chain itself is under test: a clean upgrade produces the full schema, role seeds are
applied idempotently, and the history round-trips (real downgrade(), not pass),
all against real MySQL.
"""

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import get_settings

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {"roles", "users", "articles"}


@pytest.fixture
def alembic_config(db_available: None) -> Config:
    return Config("alembic.ini")


@pytest.fixture
def sync_engine(db_available: None):
    engine = sa.create_engine(get_settings().sync_database_url)
    yield engine
    engine.dispose()


def _table_names(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def test_upgrade_head_creates_tables(alembic_config: Config, sync_engine: sa.Engine) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    tables = _table_names(sync_engine)
    assert tables >= EXPECTED_TABLES
    assert "alembic_version" in tables


def test_roles_seeded(alembic_config: Config, sync_engine: sa.Engine) -> None:
    command.upgrade(alembic_config, "head")

    with sync_engine.connect() as conn:
        names = set(conn.execute(sa.text("SELECT name FROM roles")).scalars())
    assert names == {"user", "admin"}


def test_seed_is_idempotent(alembic_config: Config, sync_engine: sa.Engine) -> None:
    command.upgrade(alembic_config, "head")

    with sync_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO roles (name) VALUES ('user'), ('admin') "
                "ON DUPLICATE KEY UPDATE name = name"
            )
        )
    with sync_engine.connect() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM roles")).scalar_one()
    assert count == 2


def test_downgrade_base_roundtrip(alembic_config: Config, sync_engine: sa.Engine) -> None:
    command.upgrade(alembic_config, "head")

    command.downgrade(alembic_config, "base")
    assert not (EXPECTED_TABLES & _table_names(sync_engine))

    command.upgrade(alembic_config, "head")
    tables = _table_names(sync_engine)
    assert tables >= EXPECTED_TABLES
    with sync_engine.connect() as conn:
        names = set(conn.execute(sa.text("SELECT name FROM roles")).scalars())
    assert names == {"user", "admin"}
