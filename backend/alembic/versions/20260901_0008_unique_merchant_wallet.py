"""Require one merchant account per wallet address.

Revision ID: 0008_unique_merchant_wallet
Revises: 0007_payment_merchant
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0008_unique_merchant_wallet"
down_revision: str | None = "0007_payment_merchant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Prevent two merchants from using the same normalized wallet."""

    op.create_unique_constraint(
        "uq_merchants_wallet_address",
        "merchants",
        ["wallet_address"],
    )


def downgrade() -> None:
    """Allow duplicate merchant wallet addresses again."""

    op.drop_constraint(
        "uq_merchants_wallet_address",
        "merchants",
        type_="unique",
    )
