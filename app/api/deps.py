"""Router dependencies: DB session, current user, and service factories."""

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import User
from app.repositories.article_repo import ArticleRepository
from app.repositories.user_repo import UserRepository
from app.services.article_service import ArticleService
from app.services.exceptions import UnauthenticatedError

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep, x_user_id: Annotated[str | None, Header()] = None
) -> User:
    if x_user_id is None or not x_user_id.isdigit():
        raise UnauthenticatedError("Missing or invalid identity.")
    user = await UserRepository(session).get_by_id(int(x_user_id))
    if user is None:
        raise UnauthenticatedError("Missing or invalid identity.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_article_service(session: SessionDep) -> ArticleService:
    return ArticleService(ArticleRepository(session))


ArticleServiceDep = Annotated[ArticleService, Depends(get_article_service)]
