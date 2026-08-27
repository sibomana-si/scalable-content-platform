"""Article business rules, with a cache-aside read path.

Public operations are wrapped in ``articles.<operation>`` domain spans so a trace reads as
what the system was doing, not only which library it was in. The wrapping is behaviour-neutral
by construction: :func:`traced` is a no-op without a tracer provider and never swallows.

Authorization and compare-and-set never read the cache. :meth:`get` is cache-aside;
:meth:`_load` always reads the database, and the write paths use only :meth:`_load`. A stale
cached ``author_id`` would decide ownership on stale data, and a stale ``updated_at`` would
break the optimistic-concurrency precondition. Everything else in this module follows from
that one rule.
"""

import asyncio
import json
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.cache.article_cache import ArticleCache
from app.cache.keys import article_key, cursor_parts, jittered_ttl, list_key
from app.db.after_commit import after_commit
from app.models import Article, User
from app.observability.tracing import traced
from app.repositories.article_repo import ArticleRepository
from app.schemas.article import ArticleOut, ArticleSummaryOut
from app.services.exceptions import ArticleNotFoundError, ConflictError, ForbiddenError

# How often a single-flight loser re-reads the key while the winner loads.
_POLL_INTERVAL_SECONDS = 0.02


# The seam the unit suite constructs the service without. A disabled cache issues no Redis
# command, so the fallback needs no client and no branch at every call site.
_NO_CACHE = ArticleCache(None, enabled=False)


class ArticleService:
    def __init__(
        self,
        articles: ArticleRepository,
        cache: ArticleCache | None = None,
        *,
        session: Any | None = None,
        article_ttl_seconds: int = 300,
        list_ttl_seconds: int = 60,
        ttl_jitter: float = 0.2,
        lock_timeout_seconds: float = 2.0,
    ) -> None:
        self._articles = articles
        self._cache = cache or _NO_CACHE
        self._session = session
        self._article_ttl = article_ttl_seconds
        self._list_ttl = list_ttl_seconds
        self._jitter = ttl_jitter
        self._lock_timeout = lock_timeout_seconds

    async def create(self, actor: User, *, title: str, body: str) -> ArticleOut:
        async with traced("articles", "create", author_id=actor.id):
            # Authorship comes from the authenticated actor, never from the payload.
            article = await self._articles.create(author_id=actor.id, title=title, body=body)
            # A new article has no cached body of its own — only the pages that must now
            # include it.
            self._invalidate_after_commit(author_id=actor.id)
            return ArticleOut.model_validate(article)

    async def get(self, article_id: int) -> ArticleOut:
        """The public read path: cache first, database on a miss.

        Concurrent misses on the same id are coalesced by a single-flight lock. A loser waits
        a bounded interval and then reads the database itself — a slow loader must cost a
        duplicate query, never a hung request.
        """
        async with traced("articles", "get", article_id=article_id):
            key = article_key(article_id)
            cached = _decode_article(await self._cache.get_article(article_id))
            if cached is not None:
                return cached

            won = await self._cache.acquire_lock(key, ttl_seconds=self._lock_ttl())
            if not won:
                waited = _decode_article(await self._await_loader(key))
                if waited is not None:
                    return waited
            try:
                dto = ArticleOut.model_validate(await self._load(article_id))
                await self._cache.set_article(
                    article_id, dto.model_dump_json(), ttl_seconds=self._article_ttl_jittered()
                )
                return dto
            finally:
                if won:
                    await self._cache.release_lock(key)

    async def list_articles(
        self, *, limit: int, after: tuple[datetime, int] | None = None, author_id: int | None = None
    ) -> tuple[list[ArticleSummaryOut], tuple[datetime, int] | None]:
        """One page plus the keyset position of the next, or ``None`` on the last page.

        The page key embeds the current generation counter, so a write invalidates every page
        it can affect with one ``INCR``.

        The page carries summaries, never bodies. That decision belongs here as much as in the
        schema: it is what keeps a cached page near 3 KB instead of 23 KB.
        """
        async with traced("articles", "list", limit=limit, paged=after is not None):
            global_gen, author_gen = await self._cache.get_generations(author_id=author_id)
            key = list_key(
                generation=author_gen if author_id is not None else global_gen,
                author_id=author_id,
                limit=limit,
                after=cursor_parts(after),
            )
            cached = _decode_page(await self._cache.get_list_page(key))
            if cached is not None:
                return cached

            won = await self._cache.acquire_lock(key, ttl_seconds=self._lock_ttl())
            if not won:
                waited = _decode_page(await self._await_loader(key))
                if waited is not None:
                    return waited
            try:
                page, next_after = await self._load_page(
                    limit=limit, after=after, author_id=author_id
                )
                await self._cache.set_list_page(
                    key, _encode_page(page, next_after), ttl_seconds=self._list_ttl_jittered()
                )
                return page, next_after
            finally:
                if won:
                    await self._cache.release_lock(key)

    async def update(
        self, actor: User, article_id: int, *, title: str, body: str, expected_updated_at: datetime
    ) -> ArticleOut:
        async with traced("articles", "update", article_id=article_id):
            article = await self._load(article_id)  # 404 for missing/soft-deleted, never cached
            self._authorize(actor, article)
            # Read the author before the CAS. update_cas expires the identity map, so touching
            # the attribute afterwards triggers a lazy reload — synchronous I/O outside the
            # greenlet context, which raises MissingGreenlet rather than returning a value.
            author_id = article.author_id
            rowcount = await self._articles.update_cas(
                article_id, expected_updated_at, title=title, body=body
            )
            if rowcount == 0:
                await self._raise_conflict_or_not_found(article_id)
            self._invalidate_after_commit(article_id=article_id, author_id=author_id)
            # Re-read from the database: MySQL advanced updated_at server-side.
            return ArticleOut.model_validate(await self._load(article_id))

    async def delete(self, actor: User, article_id: int, *, expected_updated_at: datetime) -> None:
        async with traced("articles", "delete", article_id=article_id):
            article = await self._load(article_id)
            self._authorize(actor, article)
            author_id = article.author_id  # before the CAS expires the identity map
            rowcount = await self._articles.soft_delete_cas(article_id, expected_updated_at)
            if rowcount == 0:
                await self._raise_conflict_or_not_found(article_id)
            self._invalidate_after_commit(article_id=article_id, author_id=author_id)

    def _invalidate_after_commit(self, *, author_id: int, article_id: int | None = None) -> None:
        """Queue this write's invalidation to run once the transaction commits.

        Never inline. ``get_session`` commits in its teardown, so an inline ``DEL`` would run
        before the row is durable: a concurrent reader could miss, read the pre-commit row,
        and repopulate the cache with the old value, which would then survive its full TTL
        with no error and no metric.

        The author is the article's, not the actor's. An admin editing someone else's article
        changes that author's pages, so that is the counter to move.
        """
        if self._session is None:
            return

        async def invalidate() -> None:
            if article_id is not None:
                await self._cache.invalidate_article(article_id)
            await self._cache.bump_generations(author_id=author_id)

        after_commit(self._session, invalidate)

    async def _load(self, article_id: int) -> Article:
        """Read one live article from the database. Never consults the cache."""
        article = await self._articles.get(article_id)
        if article is None:
            raise ArticleNotFoundError("Article does not exist.")
        return article

    async def _load_page(
        self, *, limit: int, after: tuple[datetime, int] | None, author_id: int | None
    ) -> tuple[list[ArticleSummaryOut], tuple[datetime, int] | None]:
        """One page from the database.

        Fetches ``limit + 1`` rows; the sentinel row proves another page exists without a
        COUNT over the table.
        """
        rows = await self._articles.list(limit=limit + 1, after=after, author_id=author_id)
        if len(rows) <= limit:
            return [ArticleSummaryOut.model_validate(row) for row in rows], None
        page = rows[:limit]
        last = page[-1]
        return [ArticleSummaryOut.model_validate(row) for row in page], (last.created_at, last.id)

    async def _await_loader(self, key: str) -> str | None:
        """Wait, bounded, for the single-flight winner to populate ``key``.

        Returns the populated body, or ``None`` once the budget runs out — at which point the
        caller reads the database itself. Never waits on the lock indefinitely.
        """
        deadline = asyncio.get_running_loop().time() + self._lock_timeout
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            body = await self._cache.peek(key)
            if body is not None:
                return body
        return None

    def _lock_ttl(self) -> int:
        return max(1, round(self._lock_timeout))

    def _article_ttl_jittered(self) -> int:
        return jittered_ttl(self._article_ttl, self._jitter)

    def _list_ttl_jittered(self) -> int:
        return jittered_ttl(self._list_ttl, self._jitter)

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


def _decode_article(body: str | None) -> ArticleOut | None:
    """Parse a cached article body, treating anything unparsable as a miss.

    A body written by an older schema must not raise into a request. It is reloaded from the
    database and overwritten on the way out.
    """
    if body is None:
        return None
    try:
        return ArticleOut.model_validate_json(body)
    except ValidationError:
        return None


def _encode_page(items: list[ArticleSummaryOut], next_after: tuple[datetime, int] | None) -> str:
    """Serialize a list page, cursor position included.

    The next-page position travels with the page. Caching the rows alone would serve a page
    whose ``next_cursor`` had to be recomputed, which means reading the database anyway.
    """
    payload = {
        "items": [item.model_dump(mode="json") for item in items],
        "next": None if next_after is None else [next_after[0].isoformat(), next_after[1]],
    }
    return json.dumps(payload)


def _decode_page(
    body: str | None,
) -> tuple[list[ArticleSummaryOut], tuple[datetime, int] | None] | None:
    """Parse a cached list page, treating anything unparsable as a miss.

    A page written before the summary projection carries a ``body`` per item. Pydantic ignores
    the extra field, so the older entry decodes into the new shape and expires on its own TTL.
    """
    if body is None:
        return None
    try:
        payload = json.loads(body)
        items = [ArticleSummaryOut.model_validate(item) for item in payload["items"]]
        raw_next = payload["next"]
    except (ValueError, KeyError, TypeError, ValidationError):
        return None
    if raw_next is None:
        return items, None
    try:
        return items, (datetime.fromisoformat(raw_next[0]), int(raw_next[1]))
    except (ValueError, TypeError, IndexError):
        return None
