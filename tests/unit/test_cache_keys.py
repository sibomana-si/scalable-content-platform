"""Unit tests for the cache key builders and TTL jitter: pure functions, no I/O.

Key shape is a contract, not an implementation detail. A key that changes shape silently
orphans every entry written under the old shape, and a key that collides serves one page's
body for another page's request. Both failures are invisible at runtime, so they are pinned
here.
"""

import pytest

from app.cache.keys import (
    ARTICLE_TTL_ENTITY,
    LIST_TTL_ENTITY,
    article_key,
    author_generation_key,
    filter_hash,
    generation_key,
    jittered_ttl,
    list_key,
    lock_key,
)

T0 = 1_752_840_000_000_000  # epoch micros, the cursor's first component


def test_article_key_shape():
    assert article_key(42) == "article:42"


def test_generation_key_shape():
    assert generation_key() == "articles:list:gen"


def test_author_generation_key_shape():
    assert author_generation_key(5) == "articles:list:gen:author:5"


def test_lock_key_shape():
    assert lock_key("article:42") == "lock:article:42"


def test_unfiltered_list_key_embeds_the_global_generation():
    key = list_key(generation=7, author_id=None, limit=20, after=None)
    assert key.startswith("articles:list:g7:all:")


def test_author_list_key_embeds_the_author_and_its_own_generation():
    key = list_key(generation=3, author_id=5, limit=20, after=None)
    assert key.startswith("articles:list:a5:g3:")


def test_a_generation_bump_makes_the_previous_key_unreachable():
    """The whole invalidation scheme rests on this: a new generation is a new key space."""
    before = list_key(generation=7, author_id=None, limit=20, after=None)
    after = list_key(generation=8, author_id=None, limit=20, after=None)
    assert before != after


def test_author_pages_never_collide_with_unfiltered_pages():
    unfiltered = list_key(generation=1, author_id=None, limit=20, after=None)
    filtered = list_key(generation=1, author_id=5, limit=20, after=None)
    assert unfiltered != filtered


def test_two_authors_at_the_same_generation_get_distinct_keys():
    assert list_key(generation=1, author_id=5, limit=20, after=None) != list_key(
        generation=1, author_id=9, limit=20, after=None
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({"limit": 20, "after": None}, {"limit": 21, "after": None}),
        ({"limit": 20, "after": None}, {"limit": 20, "after": (T0, 5)}),
        ({"limit": 20, "after": (T0, 5)}, {"limit": 20, "after": (T0, 6)}),
        ({"limit": 20, "after": (T0, 5)}, {"limit": 20, "after": (T0 + 1, 5)}),
    ],
)
def test_distinct_filters_give_distinct_hashes(left, right):
    assert filter_hash(**left) != filter_hash(**right)


def test_identical_filters_give_a_stable_hash():
    """Stable across calls, so a second reader of the same page finds the first reader's entry."""
    assert filter_hash(limit=20, after=(T0, 5)) == filter_hash(limit=20, after=(T0, 5))


def test_filter_hash_is_bounded_and_url_safe():
    """The hash goes into a Redis key; an unbounded or exotic component makes keys unwieldy."""
    value = filter_hash(limit=100, after=(T0, 2**62))
    assert 0 < len(value) <= 32
    assert value.isalnum()


def test_jittered_ttl_stays_inside_the_band():
    for _ in range(200):
        ttl = jittered_ttl(300, 0.2)
        assert 240 <= ttl <= 360


def test_jittered_ttl_with_zero_jitter_returns_the_base():
    assert jittered_ttl(300, 0.0) == 300


def test_jittered_ttl_actually_varies():
    """A constant TTL expires a whole generation of keys at once — the stampede jitter prevents."""

    values = {jittered_ttl(300, 0.2) for _ in range(200)}
    assert len(values) > 1


def test_jittered_ttl_never_returns_a_non_positive_value():
    """SETEX rejects a TTL of zero; a full-jitter setting must not be able to produce one."""
    for _ in range(200):
        assert jittered_ttl(1, 1.0) >= 1


def test_ttl_entities_are_the_closed_metric_label_set():
    assert {ARTICLE_TTL_ENTITY, LIST_TTL_ENTITY} == {"article", "list"}
