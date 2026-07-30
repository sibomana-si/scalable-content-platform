"""FR-001 acceptance: user registration.

POST /v1/auth/register creates a user with the default ``user`` role, returns 201 with
no password material in the body, and rejects a duplicate email with 409. Rigorous input
validation (email format, password policy) is covered by test_fr001_validation.py.
"""

import pytest

pytestmark = pytest.mark.integration

REGISTER = "/v1/auth/register"
VALID = {"email": "newuser@example.com", "password": "a-strong-passphrase"}


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    assert error["request_id"]
    assert "details" in error
    return error


async def test_register_returns_201_with_default_role(client):
    resp = await client.post(REGISTER, json=VALID)

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == VALID["email"]
    assert body["role"] == "user"
    assert body["id"] > 0


async def test_register_never_echoes_password(client):
    resp = await client.post(REGISTER, json=VALID)

    assert resp.status_code == 201
    assert "password" not in resp.text
    assert VALID["password"] not in resp.text


async def test_register_duplicate_email_is_409(client):
    first = await client.post(REGISTER, json=VALID)
    assert first.status_code == 201

    resp = await client.post(
        REGISTER, json={"email": VALID["email"], "password": "another-strong-1"}
    )
    assert_error(resp, 409, "CONFLICT")
