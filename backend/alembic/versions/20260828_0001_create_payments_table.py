"""Create the payments table.

Revision ID: 0001_create_payments
Revises:
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001_create_payments"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial payments table."""

    op.create_table(
        "payments",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.Numeric(precision=30, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("chain", sa.String(length=30), nullable=False),
        sa.Column("recipient_address", sa.String(length=42), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("transaction_hash", sa.String(length=66), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Remove the payments table."""

    op.drop_table("payments")
