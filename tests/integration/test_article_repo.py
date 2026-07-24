"""ArticleRepository integration tests against real MySQL.
Covers SQL correctness: soft-delete filtering and the
insert/update/delete roundtrips, with server-generated DATETIME(6) timestamps."""

from datetime import datetime

import pytest
from sqlalchemy import text

from app.repositories.article_repo import ArticleRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def repo(db_session) -> ArticleRepository:
    return ArticleRepository(db_session)


async def test_create_and_get_roundtrip(repo, db_session, user_factory):
    user = await user_factory()

    created = await repo.create(author_id=user.id, title="t", body="b")
    await db_session.commit()

    fetched = await repo.get(created.id)
    assert fetched is not None
    assert (fetched.author_id, fetched.title, fetched.body) == (user.id, "t", "b")
    assert isinstance(fetched.created_at, datetime)  # server-generated DATETIME(6)
    assert fetched.updated_at is not None


async def test_get_missing_returns_none(repo):
    assert await repo.get(424242) is None


async def test_get_excludes_soft_deleted(repo, db_session, article_factory):
    article = await article_factory(deleted_at=datetime(2026, 1, 1))
    assert await repo.get(article.id) is None


async def test_save_persists_changes_and_advances_updated_at(repo, db_session, article_factory):
    article = await article_factory()
    original_updated_at = article.updated_at

    article.title, article.body = "new title", "new body"
    await repo.save(article)
    await db_session.commit()

    fetched = await repo.get(article.id)
    assert (fetched.title, fetched.body) == ("new title", "new body")
    assert fetched.updated_at > original_updated_at  # MySQL ON UPDATE CURRENT_TIMESTAMP(6)


async def test_list_orders_newest_first_with_id_tiebreak(repo, user_factory, article_factory):
    author = await user_factory()
    t0 = datetime(2026, 7, 1, 12, 0, 0)
    older = await article_factory(author=author, created_at=t0.replace(second=1))
    tie_a = await article_factory(author=author, created_at=t0.replace(second=2))
    tie_b = await article_factory(author=author, created_at=t0.replace(second=2))

    rows = await repo.list(limit=10)

    assert [a.id for a in rows] == [tie_b.id, tie_a.id, older.id]


async def test_list_keyset_predicate_splits_ties_exactly(repo, user_factory, article_factory):
    """Continuation from inside a created_at tie must return strictly-earlier rows:
    same created_at with smaller id, then older created_at, nothing else."""

    author = await user_factory()
    t0 = datetime(2026, 7, 1, 12, 0, 0)
    older = await article_factory(author=author, created_at=t0.replace(second=1))
    ties = [await article_factory(author=author, created_at=t0.replace(second=2)) for _ in range(3)]
    newer = await article_factory(author=author, created_at=t0.replace(second=3))

    middle = sorted(ties, key=lambda a: a.id)[1]
    rows = await repo.list(limit=10, after=(middle.created_at, middle.id))

    expected = [a.id for a in ties if a.id < middle.id] + [older.id]
    assert [a.id for a in rows] == expected
    assert newer.id not in {a.id for a in rows}


async def test_list_excludes_soft_deleted_inside_window(repo, user_factory, article_factory):
    author = await user_factory()
    t0 = datetime(2026, 7, 1, 12, 0, 0)
    first = await article_factory(author=author, created_at=t0.replace(second=1))
    await article_factory(
        author=author, created_at=t0.replace(second=2), deleted_at=datetime(2026, 7, 2)
    )
    last = await article_factory(author=author, created_at=t0.replace(second=3))
    rows = await repo.list(limit=10)
    assert [a.id for a in rows] == [last.id, first.id]


async def test_list_filters_by_author(repo, user_factory, article_factory):
    alice, bob = await user_factory(), await user_factory()
    t0 = datetime(2026, 7, 1, 12, 0, 0)
    mine = await article_factory(author=alice, created_at=t0)
    await article_factory(author=bob, created_at=t0.replace(second=1))
    rows = await repo.list(limit=10, author_id=alice.id)
    assert [a.id for a in rows] == [mine.id]


async def test_list_respects_limit(repo, user_factory, article_factory):
    author = await user_factory()
    t0 = datetime(2026, 7, 1, 12, 0, 0)
    for i in range(4):
        await article_factory(author=author, created_at=t0.replace(second=i + 1))

    rows = await repo.list(limit=2)
    assert len(rows) == 2


async def test_soft_delete_marks_row_but_keeps_it(repo, db_session, article_factory):
    article = await article_factory()

    await repo.soft_delete(article)
    await db_session.commit()

    assert await repo.get(article.id) is None  # invisible to reads
    row = (
        await db_session.execute(
            text("SELECT deleted_at FROM articles WHERE id = :id"), {"id": article.id}
        )
    ).one()
    assert row.deleted_at is not None
