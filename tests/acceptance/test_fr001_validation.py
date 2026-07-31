"""FR-001 acceptance: registration input validation.

Malformed email, sub-policy / breached / oversized password, and missing fields are each
rejected with 422 and field-level details; a valid registration still succeeds (201) and a
duplicate email is a 409 (not a validation error).
"""

import pytest

pytestmark = pytest.mark.integration

REGISTER = "/v1/auth/register"
VALID = {"email": "valid@example.com", "password": "a-strong-passphrase"}


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    return error


async def test_malformed_email_is_422(client):
    resp = await client.post(
        REGISTER, json={"email": "not-an-email", "password": "a-strong-passphrase"}
    )
    error = assert_error(resp, 422, "VALIDATION_ERROR")
    assert error["details"]


async def test_short_password_is_422(client):
    resp = await client.post(REGISTER, json={"email": "a@example.com", "password": "short"})
    assert_error(resp, 422, "VALIDATION_ERROR")


async def test_breached_password_is_422(client):
    resp = await client.post(
        REGISTER, json={"email": "a@example.com", "password": "password123456"}
    )
    assert_error(resp, 422, "VALIDATION_ERROR")


async def test_oversized_password_is_422(client):
    resp = await client.post(REGISTER, json={"email": "a@example.com", "password": "a" * 129})
    assert_error(resp, 422, "VALIDATION_ERROR")


async def test_missing_password_is_422(client):
    resp = await client.post(REGISTER, json={"email": "a@example.com"})
    assert_error(resp, 422, "VALIDATION_ERROR")


async def test_valid_registration_still_201_then_duplicate_409(client):
    first = await client.post(REGISTER, json=VALID)
    assert first.status_code == 201

    dup = await client.post(REGISTER, json=VALID)
    assert_error(dup, 409, "CONFLICT")
