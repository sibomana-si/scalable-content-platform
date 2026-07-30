"""Auth endpoints: registration and login. Both are public (no token required)."""

from fastapi import APIRouter

from app.api.deps import AuthServiceDep
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register", status_code=201, response_model=UserOut)
async def register(payload: RegisterIn, service: AuthServiceDep) -> UserOut:
    # No commit here: the get_session dependency owns the request transaction.
    user = await service.register(email=payload.email, password=payload.password)
    return UserOut(id=user.id, email=user.email, role=user.role.name)


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, service: AuthServiceDep) -> TokenOut:
    token = await service.login(email=payload.email, password=payload.password)
    return TokenOut(access_token=token)
