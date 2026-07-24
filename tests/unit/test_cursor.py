"""
Unit tests for the opaque keyset cursor codec.

The cursor encodes '(created_at, id)'; microsecond-exact, URL-safe, and strictly
parsed: anything that doesn't round-trip to a well-formed pair is rejected with
'InvalidCursorError' (surfaced as 422 VALIDATION_ERROR).
"""

import base64
from datetime import datetime

import pytest

from app.api.pagination import decode_cursor, encode_cursor
from app.services.exceptions import InvalidCursorError


def test_roundtrip_preserves_microseconds_and_id():
    created_at = datetime(2026, 7, 18, 23, 59, 59, 999999)
    cursor = encode_cursor(created_at, 123456789)
    assert decode_cursor(cursor) == (created_at, 123456789)


def test_roundtrip_zero_microseconds():
    created_at = datetime(2026, 1, 1)
    assert decode_cursor(encode_cursor(created_at, 1)) == (created_at, 1)


def test_cursor_is_url_safe_and_opaque():
    cursor = encode_cursor(datetime(2026, 7, 18, 12, 30, 45, 123456), 42)

    assert cursor == cursor.strip()
    assert all(c.isalnum() or c in "-_" for c in cursor) # no padding, no reserved chars
    assert "2026" not in cursor # not a plainly readable timestamp


@pytest.mark.parametrize(
    "bad_cursor",
    [
        "",
        "not base64 !!",
        base64.urlsafe_b64encode(b"hello").decode(),    # wrong shape: no separator
        base64.urlsafe_b64encode(b"123").decode(),      # missing id part
        base64.urlsafe_b64encode(b"123:").decode(),     # empty id part
        base64.urlsafe_b64encode(b":123").decode(),     # empty timestamp part
        base64.urlsafe_b64encode(b"abc:def").decode(),  # non-integer parts
        base64.urlsafe_b64encode(b"12.5:3").decode(),   # non-integer timestamp
        base64.urlsafe_b64encode(b"1:2:3").decode(),    # too many parts
        base64.urlsafe_b64encode(b"-1:5").decode(),     # negative timestamp
        base64.urlsafe_b64encode(b"100:0").decode(),    # non-positive id
        base64.urlsafe_b64encode(b"100:-7").decode(),   # negative id
        base64.urlsafe_b64encode("100:٣".encode()).decode() # non-ASCII digit
    ]
)
def test_malformed_cursors_are_rejected(bad_cursor: str):
    with pytest.raises(InvalidCursorError):
        decode_cursor(bad_cursor)

