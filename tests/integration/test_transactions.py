"""Transaction guarantees against real MySQL."""

import asyncio
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.deps import SessionDep
from app.db.session import get_sessionmaker
from app.main import create_app
from app.models import Article
from app.repositories.article_repo import ArticleRepository

pytestmark = pytest.mark.integration


async def _fetch_row(article_id: int):
    async with get_sessionmaker()() as session:
        return (
            await session.execute(
                text("SELECT title, updated_at, deleted_at FROM articles WHERE id = :id"),
                {"id": article_id},
            )
        ).one()


# --- Compare-and-set --------------------------------------------------------------


async def test_concurrent_update_only_one_wins(article_factory):
    """
    Two writers hold the same token; the second blocks on the row lock and must lose.

    This is the lost-update race the naive read-compare-write cannot close: both reads
    see the same 'updated_at', both compares pass, last write silently wins. With CAS
    the compare happens inside the UPDATE itself.
    """

    article = await article_factory()
    token = article.updated_at

    maker = get_sessionmaker()
    async with maker() as s1, maker() as s2:
        rc1 = await ArticleRepository(s1).update_cas(
            article.id, token, title="writer one", body="b1"
        )

        async def second_writer() -> int:
            # Blocks on the InnoDB row lock until s1 commits, then re-evaluates the
            # WHERE against the committed row, where the token no longer matches.
            return await ArticleRepository(s2).update_cas(
                article.id, token, title="writer two", body="b2"
            )

        second = asyncio.create_task(second_writer())
        await asyncio.sleep(0.2)  # let the second writer reach the lock wait
        await s1.commit()
        rc2 = await second
        await s2.commit()

    assert (rc1, rc2) == (1, 0)  # exactly one winner
    row = await _fetch_row(article.id)
    assert row.title == "writer one"  # and no interleaved half-write


async def test_update_cas_with_stale_token_is_zero_rows(article_factory):
    article = await article_factory()
    stale = article.updated_at

    async with get_sessionmaker()() as session:
        repo = ArticleRepository(session)
        assert await repo.update_cas(article.id, stale, title="first", body="b") == 1
        await session.commit()

    async with get_sessionmaker()() as session:
        rc = await ArticleRepository(session).update_cas(
            article.id, stale, title="second", body="b"
        )
        await session.commit()

    assert rc == 0
    assert (await _fetch_row(article.id)).title == "first"


async def test_update_cas_missing_row_is_zero_rows(clean_db):
    async with get_sessionmaker()() as session:
        rc = await ArticleRepository(session).update_cas(
            424242, datetime(2026, 1, 1), title="t", body="b"
        )
        await session.commit()
    assert rc == 0


async def test_concurrent_delete_wins_over_update(article_factory):
    """Soft delete and update race on the same token: the update must see zero rows,
    the deleted_at IS NULL guard inside the CAS WHERE, not a separate read."""

    article = await article_factory()
    token = article.updated_at

    async with get_sessionmaker()() as session:
        assert await ArticleRepository(session).soft_delete_cas(article.id, token) == 1
        await session.commit()

    async with get_sessionmaker()() as session:
        rc = await ArticleRepository(session).update_cas(
            article.id, token, title="too late", body="b"
        )
        await session.commit()

    assert rc == 0
    row = await _fetch_row(article.id)
    assert row.deleted_at is not None
    assert row.title != "too late"


async def test_soft_delete_cas_with_stale_token_is_zero_rows(article_factory):
    article = await article_factory()
    stale = article.updated_at

    async with get_sessionmaker()() as session:
        repo = ArticleRepository(session)
        assert await repo.update_cas(article.id, stale, title="edited", body="b") == 1
        await session.commit()

    async with get_sessionmaker()() as session:
        rc = await ArticleRepository(session).soft_delete_cas(article.id, stale)
        await session.commit()

    assert rc == 0
    assert (await _fetch_row(article.id)).deleted_at is None


async def test_soft_delete_cas_is_idempotent_guarded(article_factory):
    """A second delete never matches: the first one changed both the token and
    deleted_at; double-delete surfaces are zero rows, not a silent success."""

    article = await article_factory()
    token = article.updated_at

    async with get_sessionmaker()() as session:
        repo = ArticleRepository(session)
        assert await repo.soft_delete_cas(article.id, token) == 1
        await session.commit()

    async with get_sessionmaker()() as session:
        rc = await ArticleRepository(session).soft_delete_cas(article.id, token)
        await session.commit()
    assert rc == 0


# --- Transaction-per-request boundary ---------------------------------------------------


async def test_request_rolls_back_on_unhandled_error(clean_db, user_factory, auth_headers):
    """A handler that writes then blows up must leave nothing behind, and the client
    must still receive the canonical 500 envelope."""

    user = await user_factory()
    app = create_app()

    @app.post("/_test/boom")
    async def boom(session: SessionDep) -> None:
        session.add(Article(author_id=user.id, title="doomed", body="b"))
        await session.flush()
        raise RuntimeError("kaboom after the flush")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # A token is needed only to clear the auth middleware; this route ignores the caller.
        resp = await client.post("/_test/boom", headers=auth_headers(user))

    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert "kaboom" not in error["message"]  # no internals leaked

    async with get_sessionmaker()() as session:
        count = (
            await session.execute(
                text("SELECT COUNT(*) FROM articles WHERE author_id = :a"), {"a": user.id}
            )
        ).scalar_one()
    assert count == 0  # the flushed row was rolled back.


async def test_request_commits_on_success(client, user_factory, auth_headers):
    """The happy path commits without any handler-level commit call: the row must be
    visible from a completely separate session after the response."""

    user = await user_factory()
    resp = await client.post(
        "/v1/articles", json={"title": "durable", "body": "b"}, headers=auth_headers(user)
    )
    assert resp.status_code == 201

    async with get_sessionmaker()() as session:
        title = (
            await session.execute(
                text("SELECT title FROM articles WHERE id = :id"), {"id": resp.json()["id"]}
            )
        ).scalar_one()
    assert title == "durable"


async def test_domain_error_rolls_back_cleanly(client, user_factory, auth_headers):
    """A 409 mid-request must not leave partial changes: the losing PUT's transaction
    rolls back and the winning content survives untouched."""

    user = await user_factory()
    created = (
        await client.post(
            "/v1/articles", json={"title": "original", "body": "b"}, headers=auth_headers(user)
        )
    ).json()
    first = await client.put(
        f"/v1/articles/{created['id']}",
        json={"title": "winner", "body": "b1"},
        headers={**auth_headers(user), "If-Match": created["updated_at"]},
    )
    assert first.status_code == 200

    second = await client.put(
        f"/v1/articles/{created['id']}",
        json={"title": "loser", "body": "b2"},
        headers={**auth_headers(user), "If-Match": created["updated_at"]},
    )

    assert second.status_code == 409
    assert (await _fetch_row(created["id"])).title == "winner"
