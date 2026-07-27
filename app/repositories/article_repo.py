from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article


def _utcnow() -> datetime:
    """Naive UTC, matching the DATETIME(6) columns."""

    return datetime.now(UTC).replace(tzinfo=None)


def build_list_query(
    *,
    limit: int,
    after: tuple[datetime, int] | None = None,
    author_id: int | None = None,
) -> Select[tuple[Article]]:
    """The list query as a statement, module-level so the index regression tests can
    EXPLAIN the production SQL rather than a lookalike.

    Live articles, newest first with id as deterministic tiebreaker. 'after' is
    the exclusive keyset position '(created_at, id)' of the previous page's last row:
    strictly-older rows, or same-instant rows with a smaller id.
    """

    stmt = select(Article).where(Article.deleted_at.is_(None))
    if author_id is not None:
        stmt = stmt.where(Article.author_id == author_id)
    if after is not None:
        after_created_at, after_id = after
        stmt = stmt.where(
            or_(
                Article.created_at < after_created_at,
                and_(Article.created_at == after_created_at, Article.id < after_id),
            )
        )
    return stmt.order_by(Article.created_at.desc(), Article.id.desc()).limit(limit)


class ArticleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, author_id: int, title: str, body: str) -> Article:
        article = Article(author_id=author_id, title=title, body=body)
        self._session.add(article)
        await self._session.flush()
        await self._session.refresh(article)  # pick up server-generated timestamps
        return article

    async def get(self, article_id: int) -> Article | None:
        stmt = select(Article).where(Article.id == article_id, Article.deleted_at.is_(None))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list(
        self, *, limit: int, after: tuple[datetime, int] | None = None, author_id: int | None = None
    ) -> list[Article]:
        stmt = build_list_query(limit=limit, after=after, author_id=author_id)
        return list((await self._session.execute(stmt)).scalars())

    async def update_cas(
        self, article_id: int, expected_updated_at: datetime, *, title: str, body: str
    ) -> int:
        """
        Compare-and-set full replace: one UPDATE whose WHERE carries the precondition,
        no read-modify-write window. Returns the matched-row count; 0 means stale token,
        soft-deleted, or gone.

        'updated_at' is advanced by MySQL itself (ON UPDATE CURRENT_TIMESTAMP(6)).
        """

        result = await self._session.execute(
            update(Article)
            .where(
                Article.id == article_id,
                Article.updated_at == expected_updated_at,
                Article.deleted_at.is_(None),
            )
            .values(title=title, body=body)
            .execution_options(synchronize_session=False)
        )
        # The bulk UPDATE bypasses the identity map; expire cached instances so any
        # re-read in this transaction sees the new row (incl. the server-set updated_at).
        self._session.expire_all()
        return cast(CursorResult, result).rowcount

    async def soft_delete_cas(self, article_id: int, expected_updated_at: datetime) -> int:
        """Compare-and-set soft delete; same single-statement guarantees as update."""

        result = await self._session.execute(
            update(Article)
            .where(
                Article.id == article_id,
                Article.updated_at == expected_updated_at,
                Article.deleted_at.is_(None),
            )
            .values(deleted_at=_utcnow())
            .execution_options(synchronize_session=False)
        )
        # The bulk UPDATE bypasses the identity map; expire cached instances so any
        # re-read in this transaction sees the new row (incl. the server-set updated_at).
        self._session.expire_all()
        return cast(CursorResult, result).rowcount
