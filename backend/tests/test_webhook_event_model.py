from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment
from database.models import WebhookEvent
from domain.payments import PaymentStatus
from domain.webhooks import WebhookDeliveryStatus


def make_payment() -> Payment:
    created_at = datetime.now(timezone.utc)
    return Payment(
        id="pay_webhook_test",
        amount=Decimal("0.01"),
        currency="USDC",
        chain="base-sepolia",
        recipient_address="0x2222222222222222222222222222222222222222",
        status=PaymentStatus.CONFIRMED,
        transaction_hash="0x" + "ab" * 32,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=15),
        detected_at=created_at,
        confirmed_at=created_at,
    )


def make_event(event_id: str = "evt_webhook_test") -> WebhookEvent:
    return WebhookEvent(
        id=event_id,
        event_type="payment.confirmed",
        payment_id="pay_webhook_test",
        destination_url="http://merchant.test/webhooks/stablepay",
        payload={
            "event": "payment.confirmed",
            "payment_id": "pay_webhook_test",
            "amount": "0.01",
            "currency": "USDC",
        },
    )


@pytest.mark.asyncio
async def test_webhook_event_defaults_to_pending(test_session: AsyncSession):
    test_session.add(make_payment())
    test_session.add(make_event())
    await test_session.commit()

    result = await test_session.execute(
        select(WebhookEvent).where(WebhookEvent.id == "evt_webhook_test")
    )
    event = result.scalar_one()

    assert event.status == WebhookDeliveryStatus.PENDING
    assert event.attempt_count == 0
    assert event.next_attempt_at is not None
    assert event.created_at is not None
    assert event.delivered_at is None
    assert event.last_error is None


@pytest.mark.asyncio
async def test_same_payment_event_cannot_be_queued_twice(
    test_session: AsyncSession,
):
    test_session.add(make_payment())
    test_session.add(make_event("evt_first"))
    await test_session.commit()

    test_session.add(make_event("evt_duplicate"))

    with pytest.raises(IntegrityError):
        await test_session.commit()

    await test_session.rollback()
