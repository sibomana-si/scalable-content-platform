"""Password hashing (Argon2id) and JWT access tokens (HS256).

Thin helpers over ``Settings``: no DB/network I/O. Any invalid, tampered, or expired token
surfaces as :class:`UnauthenticatedError` so the middleware and dependencies can map it to a
single generic 401 (no leakage of why a token failed).

Password hashing is the one expensive thing here and is therefore ``async``.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

from app.config import get_settings
from app.services.exceptions import UnauthenticatedError

# Argon2id with the library defaults (a sensible modern cost profile).
_hasher = PasswordHasher()


@lru_cache(maxsize=1)
def get_password_executor() -> ThreadPoolExecutor:
    """The dedicated, bounded thread pool every password hash runs in.

    Argon2id is designed to be slow and memory-hard — roughly 145 ms of CPU and 64 MiB per
    hash at the library defaults. Run inline from a coroutine it stalls the entire worker for
    that whole time, so a few concurrent logins push unrelated cached reads past the 200 ms
    P95 SLO. It releases the GIL, so a thread genuinely gets that time back.

    The pool is separate from the one Starlette uses for sync endpoints and other blocking
    calls, so a burst of logins cannot starve them, and it is bounded because unbounded
    offload would just trade the stall for a memory blow-up (N × 64 MiB). One pool per
    process, reaped by the interpreter at exit.
    """
    return ThreadPoolExecutor(
        max_workers=get_settings().password_hash_max_threads,
        thread_name_prefix="pwhash",
    )


def _hash_password_blocking(password: str) -> str:
    return _hasher.hash(password)


def _verify_password_blocking(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    # Argon2Error covers mismatch/verification failures; InvalidHashError (a ValueError)
    # covers a stored value that isn't a well-formed Argon2 hash.
    except (Argon2Error, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    """Return an Argon2id hash (embeds a random salt and the cost parameters)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_password_executor(), _hash_password_blocking, password)


async def verify_password(password: str, password_hash: str) -> bool:
    """True if password matches password_hash; False on mismatch or malformed hash."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        get_password_executor(), _verify_password_blocking, password, password_hash
    )


def create_access_token(sub: str, role: str) -> str:
    """Sign a short-lived HS256 access token carrying sub/role/iat/exp."""

    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(sub),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_expire_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.require_signing_key(), algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify and decode an access token, or raise :class:`UnauthenticatedError`.

    The algorithm allowlist is pinned to the configured algorithm, which rejects both the
    ``none`` algorithm and any algorithm-confusion attempt. Tokens missing ``sub``/``role``
    are treated as invalid.
    """

    settings = get_settings()
    try:
        claims: dict[str, Any] = jwt.decode(
            token, settings.require_signing_key(), algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise UnauthenticatedError("Invalid or expired token.") from exc

    if not claims.get("sub") or not claims.get("role"):
        raise UnauthenticatedError("Invalid or expired token.")
    return claims
