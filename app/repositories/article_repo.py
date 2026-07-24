from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article


def _utcnow() -> datetime:
    """Naive UTC, matching the DATETIME(6) columns."""

    return datetime.now(UTC).replace(tzinfo=None)


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
        """Live articles, newest first with id as deterministic tiebreaker.

        "after" is the exclusive keyset position "(created_at, id)" of the previous
        page's last row: strictly-older rows, or same-instant rows with a smaller id.
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
        stmt = stmt.order_by(Article.created_at.desc(), Article.id.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars())

    async def save(self, article: Article) -> Article:
        await self._session.flush()
        await self._session.refresh(article)  # pick up ON UPDATE CURRENT_TIMESTAMP(6)
        return article

    async def soft_delete(self, article: Article) -> None:
        article.deleted_at = _utcnow()
        await self._session.flush()
