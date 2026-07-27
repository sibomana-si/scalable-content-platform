"""add idx_articles_deleted_created

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-18

Composite index for the public article list: MySQL has no
partial indexes, so 'deleted_at' leads as a key column; 'created_at' follows for
the keyset order and InnoDB's implicit PK suffix supplies the 'id' tiebreaker.
One DDL statement per revision.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("idx_articles_deleted_created", "articles", ["deleted_at", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_articles_deleted_created", table_name="articles")
