"""Add batched merchant settlement records.

Revision ID: 0011_merchant_settlements
Revises: 0010_vault_ledger
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0011_merchant_settlements"
down_revision: str | None = "0010_vault_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


settlement_status = postgresql.ENUM(
    "pending",
    "broadcasting",
    "submitted",
    "confirmed",
    "failed",
    "review_required",
    "cancelled",
    name="settlement_status",
    create_type=False,
)


def upgrade() -> None:
    settlement_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "settlements",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("merchant_id", sa.String(length=40), nullable=False),
        sa.Column("amount_atomic", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default="USDC", nullable=False),
        sa.Column(
            "chain",
            sa.String(length=30),
            server_default="base-sepolia",
            nullable=False,
        ),
        sa.Column("destination_address", sa.String(length=42), nullable=False),
        sa.Column(
            "status",
            settlement_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "reservation_ledger_transaction_id",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column(
            "completion_ledger_transaction_id",
            sa.String(length=40),
            nullable=True,
        ),
        sa.Column("transaction_hash", sa.String(length=66), nullable=True),
        sa.Column("transaction_nonce", sa.BigInteger(), nullable=True),
        sa.Column("signed_transaction", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("broadcast_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_atomic > 0", name="ck_settlements_positive"),
        sa.ForeignKeyConstraint(
            ["completion_ledger_transaction_id"],
            ["ledger_transactions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["merchant_id"], ["merchants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reservation_ledger_transaction_id"],
            ["ledger_transactions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_id",
            "idempotency_key",
            name="uq_settlements_merchant_idempotency",
        ),
        sa.UniqueConstraint(
            "transaction_hash", name="uq_settlements_transaction_hash"
        ),
    )
    op.create_index(
        "ix_settlements_status_created_at",
        "settlements",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_settlements_merchant_created_at",
        "settlements",
        ["merchant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_settlements_merchant_created_at", table_name="settlements"
    )
    op.drop_index(
        "ix_settlements_status_created_at", table_name="settlements"
    )
    op.drop_table("settlements")
    settlement_status.drop(op.get_bind(), checkfirst=True)
