"""Make payment transaction hashes unique.

Revision ID: 0004_unique_transaction_hash
Revises: 0003_lifecycle_timestamps
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0004_unique_transaction_hash"
down_revision: str | None = "0003_lifecycle_timestamps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Prevent one transaction from paying multiple payment requests."""

    op.create_unique_constraint(
        "uq_payments_transaction_hash",
        "payments",
        ["transaction_hash"],
    )


def downgrade() -> None:
    """Allow duplicate payment transaction hashes again."""

    op.drop_constraint(
        "uq_payments_transaction_hash",
        "payments",
        type_="unique",
    )
