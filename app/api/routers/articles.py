"""Article endpoints: reads are public, writes require identity; update/delete
require the If-Match optimistic-concurrency precondition."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from app.api.deps import ArticleServiceDep, CurrentUser
from app.api.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, decode_cursor, encode_cursor
from app.schemas.article import ArticleIn, ArticleListOut, ArticleOut, ArticleSummaryOut
from app.services.exceptions import PreconditionRequiredError

router = APIRouter(prefix="/v1/articles", tags=["articles"])

IfMatchHeader = Annotated[str | None, Header()]


def _parse_if_match(if_match: str | None) -> datetime:
    """The precondition token is the updated_at value previously returned (ISO-8601
    with microseconds, HTTP-date headers only carry seconds). Quotes tolerated."""

    if if_match is None:
        raise PreconditionRequiredError(
            "If-Match header (the article's current updated_at) is required."
        )
    try:
        return datetime.fromisoformat(if_match.strip().strip('"'))
    except ValueError:
        raise PreconditionRequiredError(
            "If-Match must be the updated_at value previously returned for the article."
        ) from None


@router.post("", status_code=201, response_model=ArticleOut)
async def create_article(
    payload: ArticleIn, actor: CurrentUser, service: ArticleServiceDep
) -> ArticleOut:
    # No commit here: the get_session dependency owns the request transaction.
    return ArticleOut.model_validate(
        await service.create(actor, title=payload.title, body=payload.body)
    )


@router.get("", response_model=ArticleListOut)
async def list_articles(
    service: ArticleServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    author: Annotated[int | None, Query(ge=1)] = None,
) -> ArticleListOut:
    after = decode_cursor(cursor) if cursor is not None else None
    items, next_after = await service.list_articles(limit=limit, after=after, author_id=author)
    next_cursor = encode_cursor(*next_after) if next_after is not None else None
    return ArticleListOut(
        items=[ArticleSummaryOut.model_validate(item) for item in items], next_cursor=next_cursor
    )


@router.get("/{article_id}", response_model=ArticleOut)
async def get_article(article_id: int, service: ArticleServiceDep) -> ArticleOut:
    return ArticleOut.model_validate(await service.get(article_id))


@router.put("/{article_id}", response_model=ArticleOut)
async def update_article(
    article_id: int,
    payload: ArticleIn,
    actor: CurrentUser,
    service: ArticleServiceDep,
    if_match: IfMatchHeader = None,
) -> ArticleOut:
    expected = _parse_if_match(if_match)
    return ArticleOut.model_validate(
        await service.update(
            actor, article_id, title=payload.title, body=payload.body, expected_updated_at=expected
        )
    )


@router.delete("/{article_id}", status_code=204)
async def delete_article(
    article_id: int,
    actor: CurrentUser,
    service: ArticleServiceDep,
    if_match: IfMatchHeader = None,
) -> Response:
    expected = _parse_if_match(if_match)
    await service.delete(actor, article_id, expected_updated_at=expected)
    return Response(status_code=204)
