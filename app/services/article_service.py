"Article business rules."

from datetime import datetime

from app.models import Article, User
from app.repositories.article_repo import ArticleRepository
from app.services.exceptions import ArticleNotFoundError, ConflictError, ForbiddenError


class ArticleService:
    def __init__(self, articles: ArticleRepository, cache: object | None = None) -> None:
        self._articles = articles
        self._cache = cache

    async def create(self, actor: User, *, title: str, body: str) -> Article:
        # Authorship comes from the authenticated actor, never from the payload.
        return await self._articles.create(author_id=actor.id, title=title, body=body)

    async def get(self, article_id: int) -> Article:
        article = await self._articles.get(article_id)
        if article is None:
            raise ArticleNotFoundError("Article does not exist.")
        return article

    async def update(
            self,
            actor: User,
            article_id: int,
            *,
            title: str,
            body: str,
            expected_updated_at: datetime
    ) -> Article:
        article = await self.get(article_id)
        self._authorize(actor, article)
        self._check_precondition(article, expected_updated_at)
        article.title = title
        article.body = body
        return await self._articles.save(article)

    async def delete(self, actor: User, article_id: int, *, expected_updated_at: datetime) -> None:
        article = await self.get(article_id)
        self._authorize(actor, article)
        self._check_precondition(article, expected_updated_at)
        await self._articles.soft_delete(article)

    @staticmethod
    def _authorize(actor: User, article: Article) -> None:
        if article.author_id != actor.id and not actor.is_admin:
            raise ForbiddenError("Only the author or an admin may modify this article.")

    @staticmethod
    def _check_precondition(article: Article, expected_updated_at: datetime) -> None:
        if article.updated_at != expected_updated_at:
            raise ConflictError(
                "The article changed since it was read; re-fetch and retry with the "
                "current updated_at."
            )
