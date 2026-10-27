"""auth identity, import baseline, canonical roles and immutable audit log

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27

Data steps, all safe on a database that already holds the Milestone 1 import:
* every existing monthly value becomes its own baseline (nothing has been edited yet);
* roles imported from the workbook are renamed to the seven PRD roles (rows and user links kept);
* UPDATE and DELETE on revenue_revisions are rejected by the database itself.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANONICAL_ROLES = (
    "Seller / Account Executive",
    "Client Partner",
    "Commercial Owner",
    "CRO / Sales Leadership",
    "Finance",
    "Delivery / Resourcing",
    "Administrator",
)
# Names used by the workbook's Users sheet -> canonical names.
RENAMES = {
    "Seller / AE": "Seller / Account Executive",
    "Sales leadership": "CRO / Sales Leadership",
    "Resourcing (view only)": "Delivery / Resourcing",
    "Admin": "Administrator",
}


def upgrade() -> None:
    op.add_column(
        "project_monthly_revenue",
        sa.Column("baseline_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column("users", sa.Column("google_sub", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint(op.f("uq_users_google_sub"), "users", ["google_sub"])

    op.execute("UPDATE project_monthly_revenue SET baseline_amount = amount")

    connection = op.get_bind()
    for old, new in RENAMES.items():
        taken = connection.execute(
            sa.text("SELECT 1 FROM roles WHERE name = :new"), {"new": new}
        ).first()
        if not taken:
            connection.execute(
                sa.text("UPDATE roles SET name = :new WHERE name = :old"), {"old": old, "new": new}
            )
    for name in CANONICAL_ROLES:
        connection.execute(
            sa.text("INSERT INTO roles (name) VALUES (:name) ON CONFLICT (name) DO NOTHING"),
            {"name": name},
        )

    op.execute(
        """
        CREATE FUNCTION reject_audit_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'revenue_revisions is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER revenue_revisions_append_only
        BEFORE UPDATE OR DELETE ON revenue_revisions
        FOR EACH ROW EXECUTE FUNCTION reject_audit_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS revenue_revisions_append_only ON revenue_revisions")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_change()")
    op.drop_constraint(op.f("uq_users_google_sub"), "users", type_="unique")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "google_sub")
    op.drop_column("project_monthly_revenue", "baseline_amount")
