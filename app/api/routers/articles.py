"""Article endpoints: reads are public, writes require identity; update/delete
require the If-Match optimistic-concurrency precondition."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Response

from app.api.deps import ArticleServiceDep, CurrentUser, SessionDep
from app.schemas.article import ArticleIn, ArticleOut
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
        payload: ArticleIn, actor: CurrentUser, service: ArticleServiceDep, session: SessionDep
) -> ArticleOut:
    article = await service.create(actor, title=payload.title, body=payload.body)
    await session.commit()
    return article


@router.get("/{article_id}", response_model=ArticleOut)
async def get_article(article_id: int, service: ArticleServiceDep) -> ArticleOut:
    return await service.get(article_id)


@router.put("/{article_id}", response_model=ArticleOut)
async def update_article(
        article_id: int,
        payload: ArticleIn,
        actor: CurrentUser,
        service: ArticleServiceDep,
        session: SessionDep,
        if_match: IfMatchHeader = None
) -> ArticleOut:
    expected = _parse_if_match(if_match)
    article = await service.update(
        actor, article_id, title=payload.title, body=payload.body, expected_updated_at=expected
    )
    await session.commit()
    return article


@router.delete("/{article_id}", status_code=204)
async def delete_article(
        article_id: int,
        actor: CurrentUser,
        service: ArticleServiceDep,
        session: SessionDep,
        if_match: IfMatchHeader = None
) -> Response:
    expected = _parse_if_match(if_match)
    await service.delete(actor, article_id, expected_updated_at=expected)
    await session.commit()
    return Response(status_code=204)
