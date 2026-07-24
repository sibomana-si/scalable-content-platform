"""Article request/response schemas.

Input bounds are a validation and DoS control: title matches the column
bound; the body bound is the application limit, far below MEDIUMTEXT's 16 MiB.
Unknown request fields (e.g. author_id) are ignored: authorship is never
client-assignable.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

MAX_TITLE_LENGTH = 255
MAX_BODY_LENGTH = 100_000


class ArticleIn(BaseModel):
    """Create and PUT full-replace payload."""

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    body: str = Field(min_length=1, max_length=MAX_BODY_LENGTH)


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author_id: int
    title: str
    body: str
    created_at: datetime
    # doubles as the optimistic-concurrency token clients echo back via If-Match.
    updated_at: datetime


class ArticleListOut(BaseModel):
    items: list[ArticleOut]
    # Opaque keyset cursor for the next page; null on the last page
    next_cursor: str | None
