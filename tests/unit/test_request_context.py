"""Unit tests for the request-correlation helpers.

The request id ends up inside log lines and error envelopes, so untrusted inbound values are
sanitised before they are ever echoed — a header carrying a newline would otherwise let a
caller forge log records.
"""

import pytest
from starlette.datastructures import State

from app.observability.context import (
    UNMATCHED_ROUTE,
    new_request_id,
    principal_user_id,
    route_template,
    sanitize_request_id,
)


class _Route:
    def __init__(self, path: str) -> None:
        self.path = path


# --- sanitize_request_id --------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["abc123", "a", "A-B_c.9", "0123456789abcdef0123456789abcdef", "x" * 64],
)
def test_accepts_well_formed_ids(value: str) -> None:
    assert sanitize_request_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "x" * 65,  # over the 64-char cap
        "abc\ndef",  # log injection
        "abc\r\ndef",
        "abc\x00def",  # control character
        "abc def",  # whitespace
        "<script>",  # markup
        "id;drop",
    ],
)
def test_replaces_malformed_ids_with_a_fresh_one(value: str | None) -> None:
    generated = sanitize_request_id(value)

    assert generated != value
    assert sanitize_request_id(generated) == generated  # the replacement is itself valid


def test_generated_ids_are_unique_32_char_hex() -> None:
    ids = {new_request_id() for _ in range(100)}

    assert len(ids) == 100
    assert all(len(i) == 32 and all(c in "0123456789abcdef" for c in i) for i in ids)


# --- principal_user_id ----------------------------------------------------------------------


def test_returns_the_principal_id_as_a_string() -> None:
    assert principal_user_id(State({"principal": {"id": "7", "role": "user"}})) == "7"
    assert principal_user_id(State({"principal": {"id": 7, "role": "user"}})) == "7"


@pytest.mark.parametrize(
    "state",
    [
        State({}),  # no principal key at all (request never reached auth)
        State({"principal": None}),  # anonymous caller
        State({"principal": {}}),  # malformed: no id
        State({"principal": {"role": "user"}}),
        State({"principal": "not-a-dict"}),
    ],
)
def test_returns_none_when_there_is_no_usable_principal(state: State) -> None:
    assert principal_user_id(state) is None


# --- route_template -------------------------------------------------------------------------


def test_returns_the_templated_path_not_the_concrete_url() -> None:
    scope = {"route": _Route("/v1/articles/{article_id}"), "path": "/v1/articles/42"}
    assert route_template(scope) == "/v1/articles/{article_id}"


@pytest.mark.parametrize(
    "scope",
    [
        {},  # never matched a route (404)
        {"route": None},
        {"route": object()},  # a mount or a route without a .path
    ],
)
def test_collapses_unmatched_requests_to_a_single_constant(scope: dict) -> None:
    # Cardinality guard: unmatched paths are caller-controlled and must never become labels.
    assert route_template(scope) == UNMATCHED_ROUTE
