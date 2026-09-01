"""Connect payments to merchant accounts.

Revision ID: 0007_payment_merchant
Revises: 0006_merchants_api_keys
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0007_payment_merchant"
down_revision: str | None = "0006_merchants_api_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable ownership while preserving legacy payment records."""

    op.add_column(
        "payments",
        sa.Column("merchant_id", sa.String(length=40), nullable=True),
    )
    op.create_foreign_key(
        "fk_payments_merchant_id_merchants",
        "payments",
        "merchants",
        ["merchant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_payments_merchant_created_at",
        "payments",
        ["merchant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove payment ownership without deleting merchants or payments."""

    op.drop_index(
        "ix_payments_merchant_created_at",
        table_name="payments",
    )
    op.drop_constraint(
        "fk_payments_merchant_id_merchants",
        "payments",
        type_="foreignkey",
    )
    op.drop_column("payments", "merchant_id")
