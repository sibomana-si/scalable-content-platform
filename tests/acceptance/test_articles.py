"""Article CRUD acceptance tests."""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient, Response

from app.core.security import create_access_token

pytestmark = pytest.mark.integration

BASE = "/v1/articles"
PAYLOAD = {"title": "A title", "body": "Some body text."}


def assert_error(response: Response, status: int, code: str) -> dict:
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    assert error["request_id"]
    assert "details" in error
    return error


# --- Create -----------------------------------------------------------------------------


async def test_create_returns_201_owned_by_caller(client, user_factory, auth_headers):
    user = await user_factory()

    resp = await client.post(BASE, json=PAYLOAD, headers=auth_headers(user))

    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == PAYLOAD["title"]
    assert data["body"] == PAYLOAD["body"]
    assert data["author_id"] == user.id
    assert data["id"] > 0
    assert data["created_at"] and data["updated_at"]
    assert "password" not in resp.text and "password_hash" not in resp.text


async def test_create_without_identity_is_401(client):
    resp = await client.post(BASE, json=PAYLOAD)
    assert_error(resp, 401, "UNAUTHENTICATED")


async def test_create_with_token_for_unknown_user_is_401(client):
    # Validly-signed token, but the subject has no users row (e.g. deleted account).
    token = create_access_token(sub="999999", role="user")
    resp = await client.post(BASE, json=PAYLOAD, headers={"Authorization": f"Bearer {token}"})
    assert_error(resp, 401, "UNAUTHENTICATED")


async def test_create_with_malformed_token_is_401(client):
    resp = await client.post(BASE, json=PAYLOAD, headers={"Authorization": "Bearer not-a-real-jwt"})
    assert_error(resp, 401, "UNAUTHENTICATED")


async def test_create_with_oversized_title_is_422(client, user_factory, auth_headers):
    user = await user_factory()
    resp = await client.post(
        BASE, json={"title": "x" * 256, "body": "b"}, headers=auth_headers(user)
    )
    error = assert_error(resp, 422, "VALIDATION_ERROR")
    assert error["details"]


async def test_create_with_oversized_body_is_422(client, user_factory, auth_headers):
    user = await user_factory()
    resp = await client.post(
        BASE, json={"title": "t", "body": "x" * 100_001}, headers=auth_headers(user)
    )
    assert_error(resp, 422, "VALIDATION_ERROR")


async def test_create_with_missing_fields_is_422(client, user_factory, auth_headers):
    user = await user_factory()
    resp = await client.post(BASE, json={"title": "only a title"}, headers=auth_headers(user))
    assert_error(resp, 422, "VALIDATION_ERROR")


async def test_created_article_is_immediately_public(client, user_factory, auth_headers):
    user = await user_factory()
    created = (await client.post(BASE, json=PAYLOAD, headers=auth_headers(user))).json()

    resp = await client.get(f"{BASE}/{created['id']}")  # no auth header: anonymous read

    assert resp.status_code == 200
    assert resp.json()["title"] == PAYLOAD["title"]


# --- Read -------------------------------------------------------------------------------


async def test_get_missing_article_is_404(client):
    resp = await client.get(f"{BASE}/424242")
    assert_error(resp, 404, "ARTICLE_NOT_FOUND")


async def test_get_soft_deleted_article_is_404(client, article_factory):
    article = await article_factory(deleted_at=datetime(2026, 1, 1))
    resp = await client.get(f"{BASE}/{article.id}")
    assert_error(resp, 404, "ARTICLE_NOT_FOUND")


# --- Update (PUT full replace, If-Match precondition) -----------------------------------


async def _create_article(client, headers) -> dict:
    return (await client.post(BASE, json=PAYLOAD, headers=headers)).json()


async def test_owner_can_update_with_current_if_match(client, user_factory, auth_headers):
    user = await user_factory()
    created = await _create_article(client, auth_headers(user))

    resp = await client.put(
        f"{BASE}/{created['id']}",
        json={"title": "New title", "body": "New body."},
        headers={**auth_headers(user), "If-Match": created["updated_at"]},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "New title"
    assert data["body"] == "New body."
    assert data["updated_at"] > created["updated_at"]
    assert data["created_at"] == created["created_at"]


async def test_update_by_non_owner_is_403(client, user_factory, auth_headers):
    owner, other = await user_factory(), await user_factory()
    created = await _create_article(client, auth_headers(owner))

    resp = await client.put(
        f"{BASE}/{created['id']}",
        json={"title": "hijack", "body": "x"},
        headers={**auth_headers(other), "If-Match": created["updated_at"]},
    )
    assert_error(resp, 403, "FORBIDDEN")


async def test_admin_can_update_any_article(client, user_factory, auth_headers):
    owner = await user_factory()
    admin = await user_factory(role="admin")
    created = await _create_article(client, auth_headers(owner))

    resp = await client.put(
        f"{BASE}/{created['id']}",
        json={"title": "moderated", "body": "cleaned"},
        headers={**auth_headers(admin), "If-Match": created["updated_at"]},
    )

    assert resp.status_code == 200
    assert resp.json()["author_id"] == owner.id  # author is never changed


async def test_update_without_identity_is_401(client, user_factory, auth_headers):
    user = await user_factory()
    created = await _create_article(client, auth_headers(user))

    resp = await client.put(
        f"{BASE}/{created['id']}", json=PAYLOAD, headers={"If-Match": created["updated_at"]}
    )
    assert_error(resp, 401, "UNAUTHENTICATED")


async def test_update_without_if_match_is_422(client, user_factory, auth_headers):
    user = await user_factory()
    created = await _create_article(client, auth_headers(user))

    resp = await client.put(f"{BASE}/{created['id']}", json=PAYLOAD, headers=auth_headers(user))
    assert_error(resp, 422, "VALIDATION_ERROR")


async def test_update_with_stale_if_match_is_409(client, user_factory, auth_headers):
    user = await user_factory()
    created = await _create_article(client, auth_headers(user))
    first = await client.put(
        f"{BASE}/{created['id']}",
        json={"title": "first edit", "body": "b1"},
        headers={**auth_headers(user), "If-Match": created["updated_at"]},
    )
    assert first.status_code == 200

    resp = await client.put(
        f"{BASE}/{created['id']}",
        json={"title": "second edit from stale copy", "body": "b2"},
        headers={**auth_headers(user), "If-Match": created["updated_at"]},  # stale token
    )
    assert_error(resp, 409, "CONFLICT")


async def test_update_missing_article_is_404(client, user_factory, auth_headers):
    user = await user_factory()
    stale = datetime(2026, 1, 1).isoformat()
    resp = await client.put(
        f"{BASE}/424242", json=PAYLOAD, headers={**auth_headers(user), "If-Match": stale}
    )
    assert_error(resp, 404, "ARTICLE_NOT_FOUND")


async def test_update_cannot_change_authorship(client, user_factory, auth_headers):
    user = await user_factory()
    other = await user_factory()
    created = await _create_article(client, auth_headers(user))

    resp = await client.put(
        f"{BASE}/{created['id']}",
        json={"title": "t", "body": "b", "author_id": other.id},  # ignored field
        headers={**auth_headers(user), "If-Match": created["updated_at"]},
    )

    assert resp.status_code == 200
    assert resp.json()["author_id"] == user.id


# --- Delete (soft, If-Match precondition) -----------------------------------------------


async def test_owner_can_soft_delete(client, user_factory, auth_headers):
    user = await user_factory()
    created = await _create_article(client, auth_headers(user))

    resp = await client.delete(
        f"{BASE}/{created['id']}", headers={**auth_headers(user), "If-Match": created["updated_at"]}
    )

    assert resp.status_code == 204
    assert (await client.get(f"{BASE}/{created['id']}")).status_code == 404


async def test_admin_can_delete_any_article(client, user_factory, auth_headers):
    owner = await user_factory()
    admin = await user_factory(role="admin")
    created = await _create_article(client, auth_headers(owner))

    resp = await client.delete(
        f"{BASE}/{created['id']}",
        headers={**auth_headers(admin), "If-Match": created["updated_at"]},
    )
    assert resp.status_code == 204


async def test_delete_by_non_owner_is_403(client, user_factory, auth_headers):
    owner, other = await user_factory(), await user_factory()
    created = await _create_article(client, auth_headers(owner))

    resp = await client.delete(
        f"{BASE}/{created['id']}",
        headers={**auth_headers(other), "If-Match": created["updated_at"]},
    )
    assert_error(resp, 403, "FORBIDDEN")


async def test_delete_without_if_match_is_422(client, user_factory, auth_headers):
    user = await user_factory()
    created = await _create_article(client, auth_headers(user))

    resp = await client.delete(f"{BASE}/{created['id']}", headers=auth_headers(user))
    assert_error(resp, 422, "VALIDATION_ERROR")


async def test_delete_with_stale_if_match_is_409(client, user_factory, auth_headers):
    user = await user_factory()
    created = await _create_article(client, auth_headers(user))
    updated = await client.put(
        f"{BASE}/{created['id']}",
        json={"title": "edited", "body": "b"},
        headers={**auth_headers(user), "If-Match": created["updated_at"]},
    )
    assert updated.status_code == 200

    resp = await client.delete(
        f"{BASE}/{created['id']}",
        headers={**auth_headers(user), "If-Match": created["updated_at"]},  # stale token
    )
    assert_error(resp, 409, "CONFLICT")


async def test_double_delete_is_404(client, user_factory, auth_headers):
    user = await user_factory()
    created = await _create_article(client, auth_headers(user))
    first = await client.delete(
        f"{BASE}/{created['id']}", headers={**auth_headers(user), "If-Match": created["updated_at"]}
    )
    assert first.status_code == 204

    later = (datetime.fromisoformat(created["updated_at"]) + timedelta(seconds=1)).isoformat()
    resp = await client.delete(
        f"{BASE}/{created['id']}", headers={**auth_headers(user), "If-Match": later}
    )
    assert_error(resp, 404, "ARTICLE_NOT_FOUND")


async def test_if_match_tolerates_surrounding_quotes(
    client: AsyncClient, user_factory, auth_headers
):
    user = await user_factory()
    created = await _create_article(client, auth_headers(user))

    resp = await client.put(
        f"{BASE}/{created['id']}",
        json={"title": "quoted", "body": "b"},
        headers={**auth_headers(user), "If-Match": f'"{created["updated_at"]}"'},
    )
    assert resp.status_code == 200
