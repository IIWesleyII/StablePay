"""Create merchants and hashed API keys.

Revision ID: 0006_merchants_api_keys
Revises: 0005_webhook_events
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_merchants_api_keys"
down_revision: str | None = "0005_webhook_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add merchant ownership and API-key credential storage."""

    op.create_table(
        "merchants",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("wallet_address", sa.String(length=42), nullable=False),
        sa.Column("webhook_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "merchant_api_keys",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("merchant_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["merchant_id"],
            ["merchants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "secret_hash",
            name="uq_merchant_api_keys_secret_hash",
        ),
    )
    op.create_index(
        "ix_merchant_api_keys_merchant_revoked",
        "merchant_api_keys",
        ["merchant_id", "revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove merchant and API-key storage."""

    op.drop_index(
        "ix_merchant_api_keys_merchant_revoked",
        table_name="merchant_api_keys",
    )
    op.drop_table("merchant_api_keys")
    op.drop_table("merchants")
