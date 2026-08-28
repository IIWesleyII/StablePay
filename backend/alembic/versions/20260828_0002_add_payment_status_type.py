"""Add a controlled payment status type.

Revision ID: 0002_payment_status
Revises: 0001_create_payments
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_payment_status"
down_revision: str | None = "0001_create_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


payment_status_type = postgresql.ENUM(
    "pending",
    "confirming",
    "confirmed",
    "expired",
    name="payment_status",
    create_type=False,
)


def upgrade() -> None:
    """Replace the free-form status string with a PostgreSQL enum."""

    payment_status_type.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        "payments",
        "status",
        existing_type=sa.String(length=20),
        type_=payment_status_type,
        existing_nullable=False,
        postgresql_using="status::payment_status",
    )


def downgrade() -> None:
    """Change the status back to a free-form string."""

    op.alter_column(
        "payments",
        "status",
        existing_type=payment_status_type,
        type_=sa.String(length=20),
        existing_nullable=False,
        postgresql_using="status::text",
    )
    payment_status_type.drop(op.get_bind(), checkfirst=True)
