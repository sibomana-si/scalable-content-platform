"""Article business rules.

Public operations are wrapped in ``articles.<operation>`` domain spans so a trace reads as
what the system was doing, not only which library it was in. The wrapping is behaviour-neutral
by construction: :func:`traced` is a no-op without a tracer provider and never swallows.
"""

from datetime import datetime

from app.models import Article, User
from app.observability.tracing import traced
from app.repositories.article_repo import ArticleRepository
from app.services.exceptions import ArticleNotFoundError, ConflictError, ForbiddenError


class ArticleService:
    def __init__(self, articles: ArticleRepository, cache: object | None = None) -> None:
        self._articles = articles
        self._cache = cache

    async def create(self, actor: User, *, title: str, body: str) -> Article:
        async with traced("articles", "create", author_id=actor.id):
            # Authorship comes from the authenticated actor, never from the payload.
            return await self._articles.create(author_id=actor.id, title=title, body=body)

    async def get(self, article_id: int) -> Article:
        async with traced("articles", "get", article_id=article_id):
            article = await self._articles.get(article_id)
            if article is None:
                raise ArticleNotFoundError("Article does not exist.")
            return article

    async def list_articles(
        self, *, limit: int, after: tuple[datetime, int] | None = None, author_id: int | None = None
    ) -> tuple[list[Article], tuple[datetime, int] | None]:
        """One page plus the keyset position of the next, or 'None' on the last page.

        Fetches 'limit + 1' rows; the sentinel row proves another page exists without
        a COUNT over the table.
        """
        async with traced("articles", "list", limit=limit, paged=after is not None):
            rows = await self._articles.list(limit=limit + 1, after=after, author_id=author_id)
            if len(rows) <= limit:
                return rows, None
            page = rows[:limit]
            last = page[-1]
            return page, (last.created_at, last.id)

    async def update(
        self, actor: User, article_id: int, *, title: str, body: str, expected_updated_at: datetime
    ) -> Article:
        async with traced("articles", "update", article_id=article_id):
            article = await self.get(article_id)  # 404 for missing/soft-deleted
            self._authorize(actor, article)
            rowcount = await self._articles.update_cas(
                article_id, expected_updated_at, title=title, body=body
            )
            if rowcount == 0:
                await self._raise_conflict_or_not_found(article_id)
            return await self.get(article_id)  # re-read: MySQL advanced updated_at server-side

    async def delete(self, actor: User, article_id: int, *, expected_updated_at: datetime) -> None:
        async with traced("articles", "delete", article_id=article_id):
            article = await self.get(article_id)
            self._authorize(actor, article)
            rowcount = await self._articles.soft_delete_cas(article_id, expected_updated_at)
            if rowcount == 0:
                await self._raise_conflict_or_not_found(article_id)

    async def _raise_conflict_or_not_found(self, article_id: int) -> None:
        """
        Zero CAS rows is a stale token (409) only while the row is still live;
        otherwise the article is simply gone (404).
        """

        if await self._articles.get(article_id) is None:
            raise ArticleNotFoundError("Article does not exist.")
        raise ConflictError(
            "The article changed since it was read; re-fetch and retry with the current updated_at."
        )

    @staticmethod
    def _authorize(actor: User, article: Article) -> None:
        if article.author_id != actor.id and not actor.is_admin:
            raise ForbiddenError("Only the author or an admin may modify this article.")
