"""
Acceptance tests run the API against the real migrated database and the real cache.
``clean_db`` and ``clean_cache`` are autouse here so every test starts from an empty,
migrated schema and an empty cache; and so the whole suite skips cleanly when no MySQL
is reachable.
"""

import pytest


@pytest.fixture(autouse=True)
def _database(clean_db: None) -> None:
    """Force the migrated+clean database onto every acceptance test."""


@pytest.fixture(autouse=True)
def _cache(clean_cache: None) -> None:
    """Force an empty cache onto every acceptance test.

    Without this, a cached article from one test answers the next test's read. The row is
    gone from MySQL and the API still returns it, which reads as a phantom rather than as
    leaked state.
    """
