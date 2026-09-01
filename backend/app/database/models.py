from datetime import datetime
from datetime import timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from database.database import Base
from domain.payments import PaymentStatus
from domain.webhooks import WebhookDeliveryStatus


payment_status_type = SqlEnum(
    PaymentStatus,
    name="payment_status",
    values_callable=lambda status_enum: [status.value for status in status_enum],
    validate_strings=True,
    create_constraint=True,
)

webhook_delivery_status_type = SqlEnum(
    WebhookDeliveryStatus,
    name="webhook_delivery_status",
    values_callable=lambda status_enum: [status.value for status in status_enum],
    validate_strings=True,
    create_constraint=True,
)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint(
            "transaction_hash",
            name="uq_payments_transaction_hash",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(30, 6),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="USDC",
    )
    chain: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="base-sepolia",
    )
    recipient_address: Mapped[str] = mapped_column(
        String(42),
        nullable=False,
    )
    status: Mapped[PaymentStatus] = mapped_column(
        payment_status_type,
        nullable=False,
        default=PaymentStatus.PENDING,
    )
    transaction_hash: Mapped[str | None] = mapped_column(
        String(66),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class WebhookEvent(Base):
    """A durable merchant notification waiting to be delivered."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "event_type",
            "payment_id",
            name="uq_webhook_events_event_type_payment_id",
        ),
        Index(
            "ix_webhook_events_delivery_queue",
            "status",
            "next_attempt_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(40),
        primary_key=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    payment_id: Mapped[str] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    destination_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        webhook_delivery_status_type,
        nullable=False,
        default=WebhookDeliveryStatus.PENDING,
        server_default=WebhookDeliveryStatus.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
