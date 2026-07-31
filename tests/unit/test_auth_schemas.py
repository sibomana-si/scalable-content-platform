"""Unit tests for auth request/response schema validation.

RegisterIn enforces email format + normalization and the password policy; UserOut never
serializes any password material.
"""

import pytest
from pydantic import ValidationError

from app.schemas.auth import RegisterIn, UserOut


def test_valid_registration_normalizes_email_case():
    model = RegisterIn(email="User@Example.COM", password="a-strong-passphrase")
    assert model.email == "user@example.com"
    assert model.password == "a-strong-passphrase"


def test_malformed_email_is_rejected():
    with pytest.raises(ValidationError):
        RegisterIn(email="not-an-email", password="a-strong-passphrase")


def test_short_password_is_rejected():
    with pytest.raises(ValidationError):
        RegisterIn(email="a@example.com", password="short")


def test_breached_password_is_rejected():
    with pytest.raises(ValidationError):
        RegisterIn(email="a@example.com", password="password123456")


def test_over_length_password_is_rejected():
    with pytest.raises(ValidationError):
        RegisterIn(email="a@example.com", password="a" * 129)


def test_over_length_email_is_rejected():
    long_local = "a" * 300
    with pytest.raises(ValidationError):
        RegisterIn(email=f"{long_local}@example.com", password="a-strong-passphrase")


def test_user_out_never_serializes_password():
    dumped = UserOut(id=1, email="a@example.com", role="user").model_dump()
    assert "password" not in dumped
    assert "password_hash" not in dumped
