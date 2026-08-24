#!/usr/bin/env python
"""Seed a representative dataset for the M6 load test.

Two properties decide whether the run means anything, and both are set here.

**Volume.** The capacity model assumes about 10,000 articles of 2-8 KB. A smaller dataset fits
in the buffer pool and measures memory, not the read path.

**Skew.** The >= 90% cache hit ratio target holds only under hot-set dominated access
(``capacity-scaling-model.md``). Authorship is skewed the same way the reads are, so the author
filter meets a realistic distribution instead of a flat one.

The seeder writes through SQLAlchemy, never through the API. 10,000 POST requests are slow, they
pollute the metrics you are about to read, and they give no control over the distribution.

Run it after ``alembic upgrade head``:

```bash
python -m scripts.seed_load_dataset --articles 10000
```

The run is idempotent. A second call tops the dataset up to the requested count and adds nothing
when it is already there.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from dataclasses import dataclass

from sqlalchemy import func, insert, select

from app.db.session import get_engine, get_sessionmaker
from app.models import Article, Role, User

DEFAULT_ARTICLES = 10_000
DEFAULT_AUTHORS = 50
# 20% of the authors write 80% of the articles: the Pareto shape the capacity model assumes.
DEFAULT_HOT_SHARE = 0.2
DEFAULT_MIN_BODY_BYTES = 2_048
DEFAULT_MAX_BODY_BYTES = 8_192
DEFAULT_BATCH_SIZE = 500
DEFAULT_SEED = 1337

# Load-test accounts are named on a reserved domain so no run can collide with a real address.
AUTHOR_EMAIL_TEMPLATE = "loadtest-author-{index:04d}@loadtest.example"
# Not a hash of anything. These accounts exist to own rows; argon2 rejects the string on
# verify, so none of them can be logged into. The k6 writer registers its own account.
UNUSABLE_PASSWORD_HASH = "!load-test-account-no-password"

_FILLER = "the quick brown fox jumps over the lazy dog and files a report about it "


@dataclass(frozen=True)
class SeedPlan:
    """A validated seeding request. Construction fails rather than seeding a bad dataset."""

    articles: int
    authors: int
    hot_share: float
    min_body_bytes: int
    max_body_bytes: int
    batch_size: int
    seed: int

    def __post_init__(self) -> None:
        if self.articles < 0:
            raise ValueError(f"articles must be zero or more, got {self.articles}")
        if self.authors < 1:
            raise ValueError(f"authors must be one or more, got {self.authors}")
        if not 0.0 <= self.hot_share < 1.0:
            raise ValueError(f"hot share must be in [0, 1), got {self.hot_share}")
        if self.min_body_bytes < 1 or self.max_body_bytes < self.min_body_bytes:
            raise ValueError(
                f"body bounds must satisfy 1 <= min <= max, got "
                f"{self.min_body_bytes}-{self.max_body_bytes}"
            )
        if self.batch_size < 1:
            raise ValueError(f"batch size must be one or more, got {self.batch_size}")


@dataclass(frozen=True)
class SeedResult:
    authors_created: int
    articles_before: int
    articles_created: int

    @property
    def articles_after(self) -> int:
        return self.articles_before + self.articles_created


def plan_batches(missing: int, batch_size: int) -> list[int]:
    """Split ``missing`` rows into insert batches.

    One 10,000-row INSERT holds a lock and a redo record long enough to matter. Batches keep
    each statement short and let a failed run resume from a known count.
    """

    if batch_size < 1:
        raise ValueError(f"batch size must be one or more, got {batch_size}")
    if missing < 0:
        raise ValueError(
            f"missing must be zero or more, got {missing}; the table already holds more "
            f"articles than requested"
        )
    full, remainder = divmod(missing, batch_size)
    return [batch_size] * full + ([remainder] if remainder else [])


def build_body(rng: random.Random, min_bytes: int, max_bytes: int) -> str:
    """Return an ASCII body of a random size inside the bounds.

    ASCII keeps characters and bytes equal, so the Redis memory model in the capacity document
    measures what it says it measures.
    """

    size = rng.randint(min_bytes, max_bytes)
    repeats = size // len(_FILLER) + 1
    return (_FILLER * repeats)[:size]


def author_slots(plan: SeedPlan) -> list[int]:
    """Return one author index per article, skewed to the hot authors.

    The first ``authors x hot_share`` indexes are the hot set and take ``1 - hot_share`` of the
    articles. A hot share of zero spreads the articles evenly, which is the control case.
    """

    rng = random.Random(plan.seed)
    hot_count = int(plan.authors * plan.hot_share)
    hot_traffic = 1.0 - plan.hot_share

    slots: list[int] = []
    for _ in range(plan.articles):
        if hot_count and rng.random() < hot_traffic:
            slots.append(rng.randrange(hot_count))
        else:
            slots.append(rng.randrange(hot_count, plan.authors))
    return slots


def parse_args(argv: list[str] | None = None) -> SeedPlan:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--articles", type=int, default=DEFAULT_ARTICLES, help="target row count")
    parser.add_argument("--authors", type=int, default=DEFAULT_AUTHORS, help="author accounts")
    parser.add_argument(
        "--hot-share",
        type=float,
        default=DEFAULT_HOT_SHARE,
        help="fraction of authors who write the majority of the articles",
    )
    parser.add_argument(
        "--body-bytes",
        default=f"{DEFAULT_MIN_BODY_BYTES}-{DEFAULT_MAX_BODY_BYTES}",
        help="body size range, as MIN-MAX",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="rows per INSERT"
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="makes the dataset repeatable"
    )
    args = parser.parse_args(argv)

    low, _, high = args.body_bytes.partition("-")
    if not high:
        raise ValueError(f"body bytes must be given as MIN-MAX, got {args.body_bytes!r}")

    return SeedPlan(
        articles=args.articles,
        authors=args.authors,
        hot_share=args.hot_share,
        min_body_bytes=int(low),
        max_body_bytes=int(high),
        batch_size=args.batch_size,
        seed=args.seed,
    )


async def ensure_authors(session, plan: SeedPlan) -> tuple[list[int], int]:
    """Return the author ids in slot order, creating the accounts that do not exist yet."""

    emails = [AUTHOR_EMAIL_TEMPLATE.format(index=index) for index in range(plan.authors)]
    rows = (await session.execute(select(User.id, User.email).where(User.email.in_(emails)))).all()
    by_email = {email: user_id for user_id, email in rows}

    missing = [email for email in emails if email not in by_email]
    if missing:
        role_id = (await session.execute(select(Role.id).where(Role.name == "user"))).scalar_one()
        await session.execute(
            insert(User),
            [
                {"email": email, "password_hash": UNUSABLE_PASSWORD_HASH, "role_id": role_id}
                for email in missing
            ],
        )
        await session.commit()
        rows = (
            await session.execute(select(User.id, User.email).where(User.email.in_(emails)))
        ).all()
        by_email = {email: user_id for user_id, email in rows}

    return [by_email[email] for email in emails], len(missing)


async def seed(plan: SeedPlan) -> SeedResult:
    """Top the dataset up to ``plan.articles`` rows and return what changed."""

    rng = random.Random(plan.seed)
    async with get_sessionmaker()() as session:
        author_ids, authors_created = await ensure_authors(session, plan)

        existing = (
            await session.execute(
                select(func.count()).select_from(Article).where(Article.author_id.in_(author_ids))
            )
        ).scalar_one()

        batches = plan_batches(max(plan.articles - existing, 0), plan.batch_size)
        slots = author_slots(plan)[existing:]

        created = 0
        for size in batches:
            await session.execute(
                insert(Article),
                [
                    {
                        "author_id": author_ids[slots[created + offset]],
                        "title": f"Load test article {existing + created + offset + 1}",
                        "body": build_body(rng, plan.min_body_bytes, plan.max_body_bytes),
                    }
                    for offset in range(size)
                ],
            )
            await session.commit()
            created += size

    return SeedResult(
        authors_created=authors_created, articles_before=existing, articles_created=created
    )


async def main(argv: list[str] | None = None) -> int:
    plan = parse_args(argv)
    try:
        result = await seed(plan)
    finally:
        await get_engine().dispose()

    print(
        f"authors created {result.authors_created} · articles before {result.articles_before} · "
        f"created {result.articles_created} · total {result.articles_after}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
