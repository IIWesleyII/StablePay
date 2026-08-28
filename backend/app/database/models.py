from datetime import datetime
from datetime import timezone
from decimal import Decimal

from sqlalchemy import DateTime
from sqlalchemy import Enum as SqlEnum
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from database.database import Base
from domain.payments import PaymentStatus


payment_status_type = SqlEnum(
    PaymentStatus,
    name="payment_status",
    values_callable=lambda status_enum: [status.value for status in status_enum],
    validate_strings=True,
    create_constraint=True,
)


class Payment(Base):
    __tablename__ = "payments"

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
