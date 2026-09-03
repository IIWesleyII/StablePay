"""Add the micropayment vault and balanced internal ledger.

Revision ID: 0010_vault_ledger
Revises: 0009_blockchain_monitoring
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0010_vault_ledger"
down_revision: str | None = "0009_blockchain_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


vault_deposit_status = postgresql.ENUM(
    "pending",
    "confirmed",
    "expired",
    name="vault_deposit_status",
    create_type=False,
)
ledger_owner_type = postgresql.ENUM(
    "system",
    "vault",
    "merchant",
    name="ledger_owner_type",
    create_type=False,
)
ledger_transaction_type = postgresql.ENUM(
    "deposit",
    "micropayment",
    "settlement",
    name="ledger_transaction_type",
    create_type=False,
)


def upgrade() -> None:
    vault_deposit_status.create(op.get_bind(), checkfirst=True)
    ledger_owner_type.create(op.get_bind(), checkfirst=True)
    ledger_transaction_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "vault_challenges",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("wallet_address", sa.String(length=42), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "vaults",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("wallet_address", sa.String(length=42), nullable=False),
        sa.Column("access_token_prefix", sa.String(length=32), nullable=False),
        sa.Column("access_token_hash", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("wallet_address", name="uq_vaults_wallet_address"),
        sa.UniqueConstraint("access_token_hash", name="uq_vaults_access_token_hash"),
    )
    op.create_table(
        "ledger_accounts",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("owner_type", ledger_owner_type, nullable=False),
        sa.Column("owner_id", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default="USDC", nullable=False),
        sa.Column("balance_atomic", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_type", "owner_id", "currency",
            name="uq_ledger_accounts_owner_currency",
        ),
    )
    op.create_table(
        "ledger_transactions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("transaction_type", ledger_transaction_type, nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("source_account_id", sa.String(length=40), nullable=False),
        sa.Column("destination_account_id", sa.String(length=40), nullable=False),
        sa.Column("amount_atomic", sa.BigInteger(), nullable=False),
        sa.Column("reference_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_atomic > 0", name="ck_ledger_transactions_positive"),
        sa.ForeignKeyConstraint(
            ["source_account_id"], ["ledger_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["destination_account_id"], ["ledger_accounts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_ledger_transactions_idempotency"
        ),
    )
    op.create_index(
        "ix_ledger_transactions_created_at",
        "ledger_transactions",
        ["created_at"],
        unique=False,
    )
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("transaction_id", sa.String(length=40), nullable=False),
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("amount_atomic", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_atomic != 0", name="ck_ledger_entries_nonzero"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["ledger_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["ledger_transactions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ledger_entries_account_created_at",
        "ledger_entries",
        ["account_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "vault_deposits",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("vault_id", sa.String(length=40), nullable=False),
        sa.Column("amount_atomic", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", vault_deposit_status,
            server_default="pending", nullable=False,
        ),
        sa.Column("transaction_hash", sa.String(length=66), nullable=True),
        sa.Column("transaction_block_number", sa.BigInteger(), nullable=True),
        sa.Column("transaction_log_index", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_atomic > 0", name="ck_vault_deposits_positive"),
        sa.ForeignKeyConstraint(["vault_id"], ["vaults.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transaction_hash", "transaction_log_index",
            name="uq_vault_deposits_transaction_log",
        ),
    )
    op.create_index(
        "ix_vault_deposits_status_created_at",
        "vault_deposits",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vault_deposits_status_created_at", table_name="vault_deposits"
    )
    op.drop_table("vault_deposits")
    op.drop_index(
        "ix_ledger_entries_account_created_at", table_name="ledger_entries"
    )
    op.drop_table("ledger_entries")
    op.drop_index(
        "ix_ledger_transactions_created_at", table_name="ledger_transactions"
    )
    op.drop_table("ledger_transactions")
    op.drop_table("ledger_accounts")
    op.drop_table("vaults")
    op.drop_table("vault_challenges")

    ledger_transaction_type.drop(op.get_bind(), checkfirst=True)
    ledger_owner_type.drop(op.get_bind(), checkfirst=True)
    vault_deposit_status.drop(op.get_bind(), checkfirst=True)
