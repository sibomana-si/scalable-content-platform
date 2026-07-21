"""create roles, users, articles

Revision ID: 0001
Revises:
Create Date: 2026-07-18

Tables are created parents-first so ``foreign_key_checks`` stays ON;
each ``create_table`` prefix is a valid, harmless state.
"""

from collections.abc import Sequence
from typing import TypedDict

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class MySQLTableKwargs(TypedDict):
    mysql_engine: str
    mysql_charset: str
    mysql_collate: str


MYSQL_TABLE_KWARGS: MySQLTableKwargs = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci"
}

CURRENT_TIMESTAMP_6 = sa.text("CURRENT_TIMESTAMP(6)")
ON_UPDATE_CURRENT_TIMESTAMP_6 = sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", mysql.TINYINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6), server_default=CURRENT_TIMESTAMP_6, nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
        **MYSQL_TABLE_KWARGS
    )
    op.create_table(
        "users",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role_id", mysql.TINYINT(unsigned=True), nullable=False),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6), server_default=CURRENT_TIMESTAMP_6, nullable=False
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=ON_UPDATE_CURRENT_TIMESTAMP_6,
            nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_users_role_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT"
        ),
        **MYSQL_TABLE_KWARGS
    )
    op.create_table(
        "articles",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("author_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", mysql.MEDIUMTEXT(), nullable=False),
        sa.Column(
            "created_at", mysql.DATETIME(fsp=6), server_default=CURRENT_TIMESTAMP_6, nullable=False
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=ON_UPDATE_CURRENT_TIMESTAMP_6,
            nullable=False
        ),
        sa.Column("deleted_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_articles"),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_articles_author_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT"
        ),
        **MYSQL_TABLE_KWARGS
    )


def downgrade() -> None:
    # Children before parents so foreign_key_checks can stay ON.
    op.drop_table("articles")
    op.drop_table("users")
    op.drop_table("roles")
