"""FR-003 acceptance: role-based authorization enforced end-to-end.

Reads are public; writes require a valid token; a valid token with the wrong role/ownership
gets 403 (not a 404 leak); an authentication failure (401) always wins before any 403/404.
Per-resource ownership (author-or-admin) is enforced in the service layer.
"""

import pytest

from app.core.security import create_access_token

pytestmark = pytest.mark.integration

BASE = "/v1/articles"
PAYLOAD = {"title": "A title", "body": "Some body text."}


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    return error


async def test_public_read_requires_no_token(client, article_factory):
    article = await article_factory()
    resp = await client.get(f"{BASE}/{article.id}")
    assert resp.status_code == 200


async def test_valid_token_can_write(client, user_factory, auth_headers):
    user = await user_factory()
    resp = await client.post(BASE, json=PAYLOAD, headers=auth_headers(user))
    assert resp.status_code == 201


async def test_write_without_token_is_401(client):
    resp = await client.post(BASE, json=PAYLOAD)
    assert_error(resp, 401, "UNAUTHENTICATED")


async def test_garbage_token_is_401_before_ownership_check(client, user_factory, article_factory):
    # Another user's article + an invalid token: 401 must win before any 403/404, and the
    # article must be untouched/still visible afterward.
    owner = await user_factory()
    article = await article_factory(author=owner)

    resp = await client.put(
        f"{BASE}/{article.id}",
        json=PAYLOAD,
        headers={
            "Authorization": "Bearer not-a-real-jwt",
            "If-Match": article.updated_at.isoformat(),
        },
    )

    assert_error(resp, 401, "UNAUTHENTICATED")
    assert (await client.get(f"{BASE}/{article.id}")).status_code == 200


async def test_non_owner_write_is_403_not_404(client, user_factory, article_factory, auth_headers):
    owner = await user_factory()
    other = await user_factory()
    article = await article_factory(author=owner)

    resp = await client.put(
        f"{BASE}/{article.id}",
        json={"title": "hijack", "body": "x"},
        headers={**auth_headers(other), "If-Match": article.updated_at.isoformat()},
    )

    assert_error(resp, 403, "FORBIDDEN")
    # No 404 leak: the article still exists and is unchanged.
    assert (await client.get(f"{BASE}/{article.id}")).json()["title"] == article.title


async def test_admin_can_modify_another_users_article(
    client, user_factory, article_factory, auth_headers
):
    owner = await user_factory()
    admin = await user_factory(role="admin")
    article = await article_factory(author=owner)

    resp = await client.put(
        f"{BASE}/{article.id}",
        json={"title": "moderated", "body": "cleaned"},
        headers={**auth_headers(admin), "If-Match": article.updated_at.isoformat()},
    )

    assert resp.status_code == 200
    assert resp.json()["author_id"] == owner.id  # ownership/authorship is never reassigned


async def test_token_for_deleted_user_is_401(client):
    # Validly-signed token whose subject has no users row.
    token = create_access_token(sub="999999", role="user")
    resp = await client.post(BASE, json=PAYLOAD, headers={"Authorization": f"Bearer {token}"})
    assert_error(resp, 401, "UNAUTHENTICATED")
