"""Rules for changing payment lifecycle state.

These functions update a SQLAlchemy payment object without committing. The caller
commits once after all related work is ready, keeping the changes in one database
transaction.
"""

import re
from datetime import datetime
from datetime import timezone

from database.models import Payment
from domain.payments import PaymentStatus


TRANSACTION_HASH_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}")

ALLOWED_TRANSITIONS = {
    PaymentStatus.PENDING: {
        PaymentStatus.CONFIRMING,
        PaymentStatus.EXPIRED,
    },
    PaymentStatus.CONFIRMING: {PaymentStatus.CONFIRMED},
    PaymentStatus.CONFIRMED: set(),
    PaymentStatus.EXPIRED: set(),
}


class PaymentLifecycleError(ValueError):
    """Raised when payment lifecycle data or a transition is invalid."""


def mark_payment_detected(
    payment: Payment,
    transaction_hash: str,
    detected_at: datetime | None = None,
) -> Payment:
    """Move a pending payment to confirming and record its transaction."""

    _require_transition(payment, PaymentStatus.CONFIRMING)

    if TRANSACTION_HASH_PATTERN.fullmatch(transaction_hash) is None:
        raise PaymentLifecycleError(
            "Transaction hash must start with 0x followed by 64 hexadecimal characters"
        )

    detection_time = _as_utc(detected_at or datetime.now(timezone.utc))

    payment.status = PaymentStatus.CONFIRMING
    payment.transaction_hash = transaction_hash.lower()
    payment.detected_at = detection_time

    return payment


def mark_payment_confirmed(
    payment: Payment,
    confirmed_at: datetime | None = None,
) -> Payment:
    """Move a confirming payment to confirmed and record when it happened."""

    _require_transition(payment, PaymentStatus.CONFIRMED)

    if payment.transaction_hash is None or payment.detected_at is None:
        raise PaymentLifecycleError(
            "A payment must have detection data before it can be confirmed"
        )

    confirmation_time = _as_utc(confirmed_at or datetime.now(timezone.utc))
    detection_time = _as_utc(payment.detected_at)

    if confirmation_time < detection_time:
        raise PaymentLifecycleError(
            "Confirmation time cannot be earlier than detection time"
        )

    payment.status = PaymentStatus.CONFIRMED
    payment.confirmed_at = confirmation_time

    return payment


def mark_payment_expired(
    payment: Payment,
    expired_at: datetime | None = None,
) -> Payment:
    """Expire a pending payment after its payment deadline has passed."""

    _require_transition(payment, PaymentStatus.EXPIRED)

    expiration_time = _as_utc(payment.expires_at)
    current_time = _as_utc(expired_at or datetime.now(timezone.utc))

    if current_time < expiration_time:
        raise PaymentLifecycleError("Payment cannot expire before its expires_at time")

    payment.status = PaymentStatus.EXPIRED

    return payment


def _require_transition(payment: Payment, new_status: PaymentStatus) -> None:
    allowed_statuses = ALLOWED_TRANSITIONS[payment.status]

    if new_status not in allowed_statuses:
        raise PaymentLifecycleError(
            f"Payment cannot transition from {payment.status.value} "
            f"to {new_status.value}"
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PaymentLifecycleError("Payment lifecycle timestamps must include a timezone")

    return value.astimezone(timezone.utc)
