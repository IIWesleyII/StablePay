"""Create the durable webhook event queue.

Revision ID: 0005_webhook_events
Revises: 0004_unique_transaction_hash
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0005_webhook_events"
down_revision: str | None = "0004_unique_transaction_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


webhook_delivery_status = postgresql.ENUM(
    "pending",
    "delivered",
    "failed",
    name="webhook_delivery_status",
    create_type=False,
)


def upgrade() -> None:
    """Add storage for retryable merchant webhook deliveries."""

    webhook_delivery_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payment_id", sa.String(length=40), nullable=False),
        sa.Column("destination_url", sa.String(length=2048), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            webhook_delivery_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_type",
            "payment_id",
            name="uq_webhook_events_event_type_payment_id",
        ),
    )
    op.create_index(
        "ix_webhook_events_delivery_queue",
        "webhook_events",
        ["status", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove webhook delivery storage."""

    op.drop_index(
        "ix_webhook_events_delivery_queue",
        table_name="webhook_events",
    )
    op.drop_table("webhook_events")
    webhook_delivery_status.drop(op.get_bind(), checkfirst=True)
