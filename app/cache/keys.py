"""Cache key builders and TTL jitter.

Key shape carries the whole invalidation scheme. A list page key embeds a generation
number, so a write invalidates every page it can affect with one ``INCR``: the counter
moves, every key built from the old value becomes unreachable, and the orphans expire on
their own TTL. There is no ``SCAN`` on the write path and no key registry to keep in step.

Two counters, not one. Unfiltered pages follow a global counter; ``author=N`` pages follow a
per-author counter. A write by one author therefore leaves every other author's cached pages
intact.
"""

import hashlib
import random
from datetime import datetime

# The closed label set for the cache metrics. Defined here because the key namespace and the
# metric label are the same partition of the cache, and two lists would drift apart.
ARTICLE_TTL_ENTITY = "article"
LIST_TTL_ENTITY = "list"

_FILTER_HASH_LENGTH = 16


def article_key(article_id: int) -> str:
    """The cached article detail body."""
    return f"article:{article_id}"


def generation_key() -> str:
    """The counter that unfiltered list pages are built from."""
    return "articles:list:gen"


def author_generation_key(author_id: int) -> str:
    """The counter that ``author=N`` list pages are built from."""
    return f"articles:list:gen:author:{author_id}"


def lock_key(key: str) -> str:
    """The single-flight lock guarding one cache key."""
    return f"lock:{key}"


def filter_hash(*, limit: int, after: tuple[int, int] | None) -> str:
    """A short, stable digest of everything that defines a page window.

    ``after`` is the keyset position as ``(created_at_epoch_micros, id)`` — integers, so the
    digest cannot shift with a datetime repr change. Two pages with different windows must
    never share a key, so every component goes into the digest.
    """
    if after is None:
        material = f"l{limit}:none"
    else:
        micros, article_id = after
        material = f"l{limit}:{micros}:{article_id}"
    return hashlib.sha256(material.encode("ascii")).hexdigest()[:_FILTER_HASH_LENGTH]


def list_key(
    *, generation: int, author_id: int | None, limit: int, after: tuple[int, int] | None
) -> str:
    """One cached list page, anchored to the generation it was built under."""
    digest = filter_hash(limit=limit, after=after)
    if author_id is None:
        return f"articles:list:g{generation}:all:{digest}"
    return f"articles:list:a{author_id}:g{generation}:{digest}"


def cursor_parts(after: tuple[datetime, int] | None) -> tuple[int, int] | None:
    """Convert a keyset position to the integer pair the key builders take."""
    if after is None:
        return None
    created_at, article_id = after
    epoch = datetime(1970, 1, 1)
    delta = created_at - epoch
    micros = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    return micros, article_id


def jittered_ttl(base_seconds: int, jitter: float) -> int:
    """A TTL inside ``base ± base × jitter``, never below one second.

    Identical TTLs expire a whole generation of keys in the same instant, and every reader
    then misses at once. The spread is what keeps a mass expiry from becoming a stampede.
    """
    if jitter <= 0:
        return base_seconds
    spread = base_seconds * jitter
    return max(1, round(random.uniform(base_seconds - spread, base_seconds + spread)))
