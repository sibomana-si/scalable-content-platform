"""
Opaque keyset cursor for article lists.

The cursor is base64url of "{created_at_epoch_micros}:{id}", the last row of the
page. Microsecond-exact (DATETIME(6)), integer arithmetic end to end, so no float
precision loss. Strictly parsed: any malformed input raises InvalidCursorError
(422 VALIDATION_ERROR). Unsigned, deliberately: the worst a tampered cursor can do
is shift a window over public data.
"""

import base64
import binascii
from datetime import datetime, timedelta

from app.services.exceptions import InvalidCursorError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

_EPOCH = datetime(1970, 1, 1)


def encode_cursor(created_at: datetime, article_id: int) -> str:
    delta = created_at - _EPOCH
    micros = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    raw = f"{micros}:{article_id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded = cursor.encode("ascii") + b"=" * (-len(cursor) % 4)
        micros_part, sep, id_part = base64.urlsafe_b64decode(padded).decode("ascii").partition(":")
        if not (sep and micros_part.isdigit() and id_part.isdigit()):
            raise ValueError # ASCII digits only, no signs, spaces, or extra parts
        micros, article_id = int(micros_part), int(id_part)
        if article_id <= 0:
            raise ValueError
        return _EPOCH + timedelta(microseconds=micros), article_id
    except (ValueError, binascii.Error, UnicodeDecodeError, UnicodeEncodeError):
        raise InvalidCursorError("Invalid pagination cursor.") from None
