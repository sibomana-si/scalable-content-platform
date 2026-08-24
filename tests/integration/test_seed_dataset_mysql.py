"""The load-test seeder against real MySQL.

The unit tests prove the arithmetic. This proves the writes: the rows land, the skew survives
the round trip, and a second run is a no-op. Idempotence matters more than it looks — the run
matrix seeds before every run, and a seeder that duplicated would change the dataset between
run A and run C, which is the one thing the comparison cannot survive.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, User
from scripts.seed_load_dataset import AUTHOR_EMAIL_TEMPLATE, SeedPlan, seed

pytestmark = pytest.mark.integration


def a_plan(**overrides: object) -> SeedPlan:
    defaults: dict[str, object] = {
        "articles": 50,
        "authors": 5,
        "hot_share": 0.2,
        "min_body_bytes": 64,
        "max_body_bytes": 128,
        "batch_size": 20,
        "seed": 1337,
    }
    return SeedPlan(**{**defaults, **overrides})  # type: ignore[arg-type]


async def count_articles(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(Article))).scalar_one()


async def test_the_seeder_creates_the_requested_rows(db_session: AsyncSession) -> None:
    result = await seed(a_plan())

    assert result.articles_created == 50
    assert result.authors_created == 5
    assert await count_articles(db_session) == 50


async def test_a_second_run_adds_nothing(db_session: AsyncSession) -> None:
    await seed(a_plan())

    again = await seed(a_plan())

    assert again.articles_created == 0
    assert again.authors_created == 0
    assert await count_articles(db_session) == 50


async def test_a_second_run_tops_up_to_the_larger_count(db_session: AsyncSession) -> None:
    await seed(a_plan(articles=20))

    topped_up = await seed(a_plan(articles=50))

    assert topped_up.articles_before == 20
    assert topped_up.articles_created == 30
    assert await count_articles(db_session) == 50


async def test_the_authors_carry_the_reserved_email_domain(db_session: AsyncSession) -> None:
    """A load-test account must never be mistakable for a real one."""

    await seed(a_plan())

    emails = (await db_session.execute(select(User.email))).scalars().all()

    assert set(emails) == {AUTHOR_EMAIL_TEMPLATE.format(index=i) for i in range(5)}
    assert all(email.endswith("@loadtest.example") for email in emails)


async def test_the_written_bodies_respect_the_requested_bounds(db_session: AsyncSession) -> None:
    await seed(a_plan(min_body_bytes=64, max_body_bytes=128))

    bodies = (await db_session.execute(select(Article.body))).scalars().all()

    assert all(64 <= len(body.encode()) <= 128 for body in bodies)


async def test_the_skew_survives_the_round_trip(db_session: AsyncSession) -> None:
    """One author must dominate, or the author filter meets a flat distribution under load."""

    await seed(a_plan(articles=500, authors=10, hot_share=0.2, batch_size=100))

    counts = (
        await db_session.execute(
            select(Article.author_id, func.count()).group_by(Article.author_id)
        )
    ).all()

    per_author = sorted((count for _, count in counts), reverse=True)
    assert len(per_author) == 10
    assert sum(per_author[:2]) / sum(per_author) > 0.6
