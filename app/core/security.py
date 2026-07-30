"""Password hashing (Argon2id) and JWT access tokens (HS256).

Thin, pure helpers over ``Settings``: no DB/network I/O. Any invalid, tampered, or expired
token surfaces as :class:`UnauthenticatedError` so the middleware and dependencies can map it
to a single generic 401 (no leakage of why a token failed).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

from app.config import get_settings
from app.services.exceptions import UnauthenticatedError

# Argon2id with the library defaults (a sensible modern cost profile).
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return an Argon2id hash (embeds a random salt and the cost parameters)."""

    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """True if password matches password_hash; False on mismatch or malformed hash."""

    try:
        return _hasher.verify(password_hash, password)
    # Argon2Error covers mismatch/verification failures; InvalidError (a ValueError)
    # covers a stored value that isn't a well-formed Argon2 hash.
    except (Argon2Error, InvalidHashError):
        return False


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
