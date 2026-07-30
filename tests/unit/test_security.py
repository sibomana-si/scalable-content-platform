"""Unit tests for password hashing (Argon2id) and JWT access tokens (HS256).

Pure functions, no I/O beyond reading Settings; an autouse fixture pins a signing key
from the environment so the tests are hermetic. Negative security cases (tampered
signature, wrong algorithm, expiry, missing claims) are first-class.
"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.services.exceptions import UnauthenticatedError

SIGNING_KEY = "unit-test-signing-key"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[misc]
    monkeypatch.setenv("JWT_SECRET", SIGNING_KEY)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("JWT_EXPIRE_SECONDS", "900")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _future() -> int:
    return int((datetime.now(UTC) + timedelta(hours=1)).timestamp())


# --- Password hashing ---------------------------------------------------------------------


def test_hash_is_not_plaintext() -> None:
    digest = hash_password("correct horse battery staple")
    assert digest != "correct horse battery staple"
    assert digest.startswith("$argon2")


def test_verify_round_trip() -> None:
    digest = hash_password("s3cret-password!!")
    assert verify_password("s3cret-password!!", digest) is True


def test_verify_rejects_wrong_password() -> None:
    digest = hash_password("s3cret-password!!")
    assert verify_password("not-the-password", digest) is False


def test_same_password_hashes_differ() -> None:
    # Random per-hash salt: two hashes of the same input must not be equal.
    assert hash_password("same-password-123") != hash_password("same-password-123")


def test_verify_handles_malformed_hash() -> None:
    assert verify_password("whatever", "not-a-valid-argon2-hash") is False


# --- JWT access tokens --------------------------------------------------------------------


def test_access_token_round_trips_claims() -> None:
    token = create_access_token(sub="42", role="user")
    claims = decode_access_token(token)
    assert claims["sub"] == "42"
    assert claims["role"] == "user"
    assert "iat" in claims and "exp" in claims


def test_expired_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_EXPIRE_SECONDS", "-1")  # exp already in the past
    get_settings.cache_clear()
    token = create_access_token(sub="42", role="user")
    with pytest.raises(UnauthenticatedError):
        decode_access_token(token)


def test_token_signed_with_other_key_is_rejected() -> None:
    forged = jwt.encode(
        {"sub": "1", "role": "admin", "exp": _future()}, "attacker-key", algorithm="HS256"
    )
    with pytest.raises(UnauthenticatedError):
        decode_access_token(forged)


def test_token_with_unexpected_algorithm_is_rejected() -> None:
    # Correct key but HS512, algorithm-confusion attempt must fail the HS256-only allowlist.
    forged = jwt.encode(
        {"sub": "1", "role": "user", "exp": _future()}, SIGNING_KEY, algorithm="HS512"
    )
    with pytest.raises(UnauthenticatedError):
        decode_access_token(forged)


def test_unsigned_none_algorithm_token_is_rejected() -> None:
    forged = jwt.encode({"sub": "1", "role": "admin", "exp": _future()}, None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(UnauthenticatedError):
        decode_access_token(forged)


def test_missing_sub_claim_is_rejected() -> None:
    token = jwt.encode({"role": "user", "exp": _future()}, SIGNING_KEY, algorithm="HS256")
    with pytest.raises(UnauthenticatedError):
        decode_access_token(token)


def test_missing_role_claim_is_rejected() -> None:
    token = jwt.encode({"sub": "1", "exp": _future()}, SIGNING_KEY, algorithm="HS256")
    with pytest.raises(UnauthenticatedError):
        decode_access_token(token)
