"""add idx_articles_author_deleted_created

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18

Composite index for the author-filtered article list: equality
on 'author_id' leads, then 'deleted_at', then
'created_at' for the keyset order. One DDL statement per revision.

InnoDB FK side effect: creating this index makes it the supporting index for
'fk_articles_author_id' and MySQL silently drops the implicit single-column index it
had auto-created for that FK. The downgrade therefore can't just drop this index
(error 1553); it drops the FK, drops the index, and recreates the FK so MySQL
re-creates its implicit index; restoring the exact pre-upgrade state, which keeps
repeated downgrade/upgrade cycles convergent.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_articles_author_deleted_created", "articles", ["author_id", "deleted_at", "created_at"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_articles_author_id", "articles", type_="foreignkey")
    op.drop_index("idx_articles_author_deleted_created", table_name="articles")
    op.create_foreign_key(
        "fk_articles_author_id",
        "articles",
        "users",
        ["author_id"],
        ["id"],
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )
