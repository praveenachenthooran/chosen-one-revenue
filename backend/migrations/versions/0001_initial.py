"""Initialize the migration chain without business tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-27
"""

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
