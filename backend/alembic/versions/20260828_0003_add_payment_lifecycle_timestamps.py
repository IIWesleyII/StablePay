"""Add payment lifecycle timestamps.

Revision ID: 0003_lifecycle_timestamps
Revises: 0002_payment_status
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003_lifecycle_timestamps"
down_revision: str | None = "0002_payment_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add and initialize timestamps used throughout the payment lifecycle."""

    op.add_column(
        "payments",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Existing payments predate expiration support. Give each one the original
    # default window before making the column required.
    op.execute(
        "UPDATE payments "
        "SET expires_at = created_at + INTERVAL '15 minutes' "
        "WHERE expires_at IS NULL"
    )
    op.alter_column(
        "payments",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )


def downgrade() -> None:
    """Remove payment lifecycle timestamps."""

    op.drop_column("payments", "confirmed_at")
    op.drop_column("payments", "detected_at")
    op.drop_column("payments", "expires_at")
