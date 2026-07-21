"""seed roles

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18

Idempotent seed data migration: the ``user`` and ``admin`` rows are
reference data production needs too, so they live in the migration chain.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO roles (name) VALUES ('user'), ('admin') "
        "ON DUPLICATE KEY UPDATE name = name" # idempotent via uq_roles_name
    )

def downgrade() -> None:
    op.execute("DELETE FROM roles WHERE name IN ('user', 'admin')")
