"""Inner-loop unit tests for ArticleService: pure logic, no I/O.

The repository here is an in-memory fake; the real repository is covered by integration
tests against MySQL. These tests pin ownership rules, the
optimistic-concurrency decisions, and authorship immutability.
"""

import itertools
from datetime import datetime, timedelta

import pytest

from app.models import Article, Role, User
from app.services.article_service import ArticleService
from app.services.exceptions import ArticleNotFoundError, ConflictError, ForbiddenError

T0 = datetime(2026, 7, 18, 12, 0, 0)


class FakeArticleRepository:
    """In-memory stand-in mirroring ArticleRepository's interface."""

    def __init__(self) -> None:
        self.rows: dict[int, Article] = {}
        self._ids = itertools.count(1)
        self._clock = itertools.count(1)

    def _now(self) -> datetime:
        return T0 + timedelta(seconds=next(self._clock))

    async def create(self, *, author_id: int, title: str, body: str) -> Article:
        now = self._now()
        article = Article(
            id=next(self._ids),
            author_id=author_id,
            title=title,
            body=body,
            created_at=now,
            updated_at=now,
        )
        self.rows[article.id] = article
        return article

    async def get(self, article_id: int) -> Article | None:
        article = self.rows.get(article_id)
        if article is None or article.deleted_at is not None:
            return None
        return article

    async def save(self, article: Article) -> Article:
        article.updated_at = self._now()
        return article

    async def soft_delete(self, article: Article) -> None:
        article.deleted_at = self._now()


def make_user(user_id: int, role: str = "user") -> User:
    return User(
        id=user_id,
        email=f"u{user_id}@example.com",
        password_hash="x",
        role=Role(id=1 if role == "user" else 2, name=role),
    )


@pytest.fixture
def repo() -> FakeArticleRepository:
    return FakeArticleRepository()


@pytest.fixture
def service(repo: FakeArticleRepository) -> ArticleService:
    return ArticleService(repo)


@pytest.fixture
def owner() -> User:
    return make_user(1)


async def test_create_assigns_author_from_actor(service, owner):
    article = await service.create(owner, title="t", body="b")
    assert article.author_id == owner.id


async def test_get_returns_article(service, owner):
    created = await service.create(owner, title="t", body="b")
    assert (await service.get(created.id)).id == created.id


async def test_get_missing_raises_not_found(service):
    with pytest.raises(ArticleNotFoundError):
        await service.get(404)


async def test_get_soft_deleted_raises_not_found(service, repo, owner):
    created = await service.create(owner, title="t", body="b")
    await repo.soft_delete(created)
    with pytest.raises(ArticleNotFoundError):
        await service.get(created.id)


async def test_owner_can_update(service, owner):
    created = await service.create(owner, title="t", body="b")
    updated = await service.update(
        owner, created.id, title="t2", body="b2", expected_updated_at=created.updated_at
    )
    assert (updated.title, updated.body) == ("t2", "b2")


async def test_update_never_changes_author(service, owner):
    created = await service.create(owner, title="t", body="b")
    updated = await service.update(
        owner, created.id, title="t2", body="b2", expected_updated_at=created.updated_at
    )
    assert updated.author_id == owner.id


async def test_non_owner_cannot_update(service, owner):
    created = await service.create(owner, title="t", body="b")
    with pytest.raises(ForbiddenError):
        await service.update(
            make_user(2), created.id, title="x", body="x", expected_updated_at=created.updated_at
        )


async def test_admin_can_update_any(service, owner):
    created = await service.create(owner, title="t", body="b")
    updated = await service.update(
        make_user(3, role="admin"),
        created.id,
        title="mod",
        body="mod",
        expected_updated_at=created.updated_at,
    )
    assert updated.title == "mod"
    assert updated.author_id == owner.id


async def test_update_with_stale_token_raises_conflict(service, owner):
    created = await service.create(owner, title="t", body="b")
    stale = created.updated_at
    await service.update(owner, created.id, title="t2", body="b2", expected_updated_at=stale)

    with pytest.raises(ConflictError):
        await service.update(owner, created.id, title="t3", body="b3", expected_updated_at=stale)


async def test_update_missing_article_raises_not_found(service, owner):
    with pytest.raises(ArticleNotFoundError):
        await service.update(owner, 404, title="t", body="b", expected_updated_at=T0)


async def test_owner_can_delete(service, owner):
    created = await service.create(owner, title="t", body="b")
    await service.delete(owner, created.id, expected_updated_at=created.updated_at)
    with pytest.raises(ArticleNotFoundError):
        await service.get(created.id)


async def test_non_owner_cannot_delete(service, owner):
    created = await service.create(owner, title="t", body="b")
    with pytest.raises(ForbiddenError):
        await service.delete(make_user(2), created.id, expected_updated_at=created.updated_at)


async def test_admin_can_delete_any(service, owner):
    created = await service.create(owner, title="t", body="b")
    await service.delete(
        make_user(3, role="admin"), created.id, expected_updated_at=created.updated_at
    )
    with pytest.raises(ArticleNotFoundError):
        await service.get(created.id)


async def test_delete_with_stale_token_raises_conflict(service, owner):
    created = await service.create(owner, title="t", body="b")
    stale = created.updated_at
    await service.update(owner, created.id, title="t2", body="b2", expected_updated_at=stale)

    with pytest.raises(ConflictError):
        await service.delete(owner, created.id, expected_updated_at=stale)


async def test_delete_already_deleted_raises_not_found(service, owner):
    created = await service.create(owner, title="t", body="b")
    await service.delete(owner, created.id, expected_updated_at=created.updated_at)
    with pytest.raises(ArticleNotFoundError):
        await service.delete(owner, created.id, expected_updated_at=created.updated_at)
