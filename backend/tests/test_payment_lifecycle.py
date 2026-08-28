from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment
from domain.payments import PaymentStatus
from services.payment_lifecycle import PaymentLifecycleError
from services.payment_lifecycle import mark_payment_confirmed
from services.payment_lifecycle import mark_payment_detected
from services.payment_lifecycle import mark_payment_expired


CREATED_AT = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
EXPIRES_AT = CREATED_AT + timedelta(minutes=15)
TRANSACTION_HASH = "0x" + "a1" * 32


def make_payment(status: PaymentStatus = PaymentStatus.PENDING) -> Payment:
    return Payment(
        id="pay_lifecycle_test",
        amount=Decimal("1.00"),
        currency="USDC",
        chain="base-sepolia",
        recipient_address="0x1111111111111111111111111111111111111111",
        status=status,
        created_at=CREATED_AT,
        expires_at=EXPIRES_AT,
    )


def test_detecting_payment_updates_related_fields():
    payment = make_payment()
    detected_at = CREATED_AT + timedelta(minutes=1)

    uppercase_transaction_hash = "0x" + "A1" * 32
    mark_payment_detected(payment, uppercase_transaction_hash, detected_at)

    assert payment.status is PaymentStatus.CONFIRMING
    assert payment.transaction_hash == TRANSACTION_HASH
    assert payment.detected_at == detected_at
    assert payment.confirmed_at is None


def test_invalid_transaction_hash_is_rejected():
    payment = make_payment()

    with pytest.raises(PaymentLifecycleError, match="Transaction hash"):
        mark_payment_detected(payment, "not-a-transaction-hash", CREATED_AT)

    assert payment.status is PaymentStatus.PENDING
    assert payment.transaction_hash is None
    assert payment.detected_at is None


def test_confirming_payment_can_be_confirmed():
    payment = make_payment()
    detected_at = CREATED_AT + timedelta(minutes=1)
    confirmed_at = CREATED_AT + timedelta(minutes=2)
    mark_payment_detected(payment, TRANSACTION_HASH, detected_at)

    mark_payment_confirmed(payment, confirmed_at)

    assert payment.status is PaymentStatus.CONFIRMED
    assert payment.confirmed_at == confirmed_at


def test_pending_payment_cannot_skip_directly_to_confirmed():
    payment = make_payment()

    with pytest.raises(
        PaymentLifecycleError,
        match="cannot transition from pending to confirmed",
    ):
        mark_payment_confirmed(payment, CREATED_AT)

    assert payment.status is PaymentStatus.PENDING
    assert payment.confirmed_at is None


def test_confirmation_cannot_predate_detection():
    payment = make_payment()
    detected_at = CREATED_AT + timedelta(minutes=2)
    mark_payment_detected(payment, TRANSACTION_HASH, detected_at)

    with pytest.raises(PaymentLifecycleError, match="earlier than detection"):
        mark_payment_confirmed(payment, CREATED_AT + timedelta(minutes=1))

    assert payment.status is PaymentStatus.CONFIRMING
    assert payment.confirmed_at is None


def test_pending_payment_can_expire_at_its_deadline():
    payment = make_payment()

    mark_payment_expired(payment, EXPIRES_AT)

    assert payment.status is PaymentStatus.EXPIRED


def test_payment_cannot_expire_early():
    payment = make_payment()

    with pytest.raises(PaymentLifecycleError, match="before its expires_at"):
        mark_payment_expired(payment, EXPIRES_AT - timedelta(seconds=1))

    assert payment.status is PaymentStatus.PENDING


@pytest.mark.parametrize(
    "terminal_status",
    [PaymentStatus.CONFIRMED, PaymentStatus.EXPIRED],
)
def test_terminal_payment_cannot_transition(terminal_status: PaymentStatus):
    payment = make_payment(status=terminal_status)

    with pytest.raises(PaymentLifecycleError, match="cannot transition"):
        mark_payment_detected(payment, TRANSACTION_HASH, CREATED_AT)

    assert payment.status is terminal_status


def test_naive_timestamp_is_rejected():
    payment = make_payment()
    timestamp_without_timezone = datetime(2026, 8, 28, 12, 1)

    with pytest.raises(PaymentLifecycleError, match="must include a timezone"):
        mark_payment_detected(
            payment,
            TRANSACTION_HASH,
            timestamp_without_timezone,
        )

    assert payment.status is PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_detection_fields_are_persisted_together(
    test_session: AsyncSession,
):
    payment = make_payment()
    test_session.add(payment)
    await test_session.commit()

    mark_payment_detected(
        payment,
        TRANSACTION_HASH,
        CREATED_AT + timedelta(minutes=1),
    )
    await test_session.commit()
    await test_session.refresh(payment)

    assert payment.status is PaymentStatus.CONFIRMING
    assert payment.transaction_hash == TRANSACTION_HASH
    assert payment.detected_at is not None
