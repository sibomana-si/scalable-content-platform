"""FR-002 acceptance: login issues a JWT; that JWT authenticates protected writes.

POST /v1/auth/login returns a bearer access token for valid credentials and a generic
401 (no user enumeration) otherwise. The issued token then authorizes an article write,
while a missing / garbage / expired token on a write is rejected with 401.
"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import get_settings

pytestmark = pytest.mark.integration

REGISTER = "/v1/auth/register"
LOGIN = "/v1/auth/login"
ARTICLES = "/v1/articles"
CREDS = {"email": "member@example.com", "password": "a-strong-passphrase"}
ARTICLE = {"title": "Written by a token holder", "body": "Body text."}


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    return error


async def _register(client) -> None:
    resp = await client.post(REGISTER, json=CREDS)
    assert resp.status_code == 201


async def test_login_returns_bearer_token(client):
    await _register(client)

    resp = await client.post(LOGIN, json=CREDS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_login_wrong_password_is_401(client):
    await _register(client)
    resp = await client.post(LOGIN, json={"email": CREDS["email"], "password": "wrong-password"})
    assert_error(resp, 401, "UNAUTHENTICATED")


async def test_login_unknown_email_is_401(client):
    resp = await client.post(
        LOGIN, json={"email": "ghost@example.com", "password": "whatever-pass"}
    )
    assert_error(resp, 401, "UNAUTHENTICATED")


async def test_issued_token_authorizes_article_write(client):
    await _register(client)
    token = (await client.post(LOGIN, json=CREDS)).json()["access_token"]

    resp = await client.post(ARTICLES, json=ARTICLE, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 201
    assert resp.json()["title"] == ARTICLE["title"]


async def test_write_without_token_is_401(client):
    resp = await client.post(ARTICLES, json=ARTICLE)
    assert_error(resp, 401, "UNAUTHENTICATED")


async def test_write_with_garbage_token_is_401(client):
    resp = await client.post(
        ARTICLES, json=ARTICLE, headers={"Authorization": "Bearer not-a-real-jwt"}
    )
    assert_error(resp, 401, "UNAUTHENTICATED")


async def test_write_with_expired_token_is_401(client):
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": "1",
            "role": "user",
            "exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
        },
        settings.require_signing_key(),
        algorithm=settings.jwt_algorithm,
    )
    resp = await client.post(ARTICLES, json=ARTICLE, headers={"Authorization": f"Bearer {expired}"})
    assert_error(resp, 401, "UNAUTHENTICATED")
