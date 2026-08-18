"""
Integration tests run against the real containers, so they share the real cache.

``clean_cache`` is autouse here for the same reason it is in the acceptance suite: Redis
outlives a test. A page cached by one test answers the next test's read, and the second test
then sees no database span, no query, and no explanation.
"""

import pytest


@pytest.fixture(autouse=True)
def _cache(clean_cache: None) -> None:
    """Force an empty cache onto every integration test."""
