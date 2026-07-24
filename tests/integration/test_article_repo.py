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
