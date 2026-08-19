"""Router dependencies: DB session, current user, and service factories."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.article_cache import ArticleCache
from app.cache.client import get_redis
from app.config import get_settings
from app.db.session import get_session
from app.models import User
from app.repositories.article_repo import ArticleRepository
from app.repositories.user_repo import UserRepository
from app.services.article_service import ArticleService
from app.services.auth_service import AuthService
from app.services.exceptions import UnauthenticatedError

# scope="function" is load-bearing, not a stylistic choice. FastAPI defaults a dependency with
# yield to scope="request", which ends it after the response has been sent — the commit in
# get_session would then land behind the response, and a 201 would announce a row the next
# request cannot yet see. "function" ends it before the response leaves the router.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


async def get_current_user(request: Request, session: SessionDep) -> User:
    # The auth middleware has already verified the JWT and attached the principal;
    # here we resolve it to the live users row (a since-deleted user -> 401).
    principal = getattr(request.state, "principal", None)
    if not principal:
        raise UnauthenticatedError("Missing or invalid identity.")
    user = await UserRepository(session).get_by_id(int(principal["id"]))
    if user is None:
        raise UnauthenticatedError("Missing or invalid identity.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_article_service(session: SessionDep) -> ArticleService:
    settings = get_settings()
    cache = ArticleCache(get_redis(), enabled=settings.cache_enabled)
    return ArticleService(
        ArticleRepository(session),
        cache,
        session=session,
        article_ttl_seconds=settings.cache_article_ttl_seconds,
        list_ttl_seconds=settings.cache_list_ttl_seconds,
        ttl_jitter=settings.cache_ttl_jitter,
        lock_timeout_seconds=settings.cache_lock_timeout_seconds,
    )


ArticleServiceDep = Annotated[ArticleService, Depends(get_article_service)]


def get_auth_service(session: SessionDep) -> AuthService:
    return AuthService(UserRepository(session))


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
