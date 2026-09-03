"""Add durable blockchain monitoring state.

Revision ID: 0009_blockchain_monitoring
Revises: 0008_unique_merchant_wallet
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0009_blockchain_monitoring"
down_revision: str | None = "0008_unique_merchant_wallet"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store scan progress and the exact matched transfer location."""

    op.add_column(
        "payments",
        sa.Column("payer_address", sa.String(length=42), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("transaction_block_number", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column("transaction_log_index", sa.Integer(), nullable=True),
    )

    op.create_table(
        "blockchain_cursors",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("chain", sa.String(length=30), nullable=False),
        sa.Column("token_address", sa.String(length=42), nullable=False),
        sa.Column("last_scanned_block", sa.BigInteger(), nullable=False),
        sa.Column("last_scanned_block_hash", sa.String(length=66), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Remove blockchain monitor progress and transfer audit fields."""

    op.drop_table("blockchain_cursors")
    op.drop_column("payments", "transaction_log_index")
    op.drop_column("payments", "transaction_block_number")
    op.drop_column("payments", "payer_address")
