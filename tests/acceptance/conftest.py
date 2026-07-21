"""
Acceptance tests run the API against the real migrated database.
``clean_db`` is autouse here so every test starts from an empty, migrated
schema; and so the whole suite skips cleanly when no MySQL is reachable.
"""

import pytest


@pytest.fixture(autouse=True)
def _database(clean_db: None) -> None:
    """Force the migrated+clean database onto every acceptance test."""
