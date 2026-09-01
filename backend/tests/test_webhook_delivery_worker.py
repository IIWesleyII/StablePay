from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment
from database.models import WebhookEvent
from domain.payments import PaymentStatus
from domain.webhooks import WebhookDeliveryStatus
from workers.webhook_delivery import claim_due_webhook_events
from workers.webhook_delivery import deliver_claimed_webhook_event


CURRENT_TIME = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
WEBHOOK_SECRET = "test-webhook-secret-that-is-at-least-32-characters"


def make_payment() -> Payment:
    return Payment(
        id="pay_worker_test",
        amount=Decimal("0.01"),
        currency="USDC",
        chain="base-sepolia",
        recipient_address="0x2222222222222222222222222222222222222222",
        status=PaymentStatus.CONFIRMED,
        transaction_hash="0x" + "ab" * 32,
        created_at=CURRENT_TIME - timedelta(minutes=1),
        expires_at=CURRENT_TIME + timedelta(minutes=14),
        detected_at=CURRENT_TIME - timedelta(seconds=5),
        confirmed_at=CURRENT_TIME,
    )


def make_event(
    event_id: str,
    next_attempt_at: datetime,
    status: WebhookDeliveryStatus = WebhookDeliveryStatus.PENDING,
) -> WebhookEvent:
    return WebhookEvent(
        id=event_id,
        event_type=event_id.replace("evt_", "payment."),
        payment_id="pay_worker_test",
        destination_url="https://merchant.test/webhooks/stablepay",
        payload={"id": event_id, "type": "payment.confirmed"},
        status=status,
        attempt_count=0,
        next_attempt_at=next_attempt_at,
        created_at=CURRENT_TIME - timedelta(minutes=1),
    )


@pytest.mark.asyncio
async def test_worker_claims_only_due_pending_events(
    test_session: AsyncSession,
):
    due = make_event("evt_due", CURRENT_TIME - timedelta(seconds=1))
    future = make_event("evt_future", CURRENT_TIME + timedelta(seconds=1))
    delivered = make_event(
        "evt_delivered",
        CURRENT_TIME - timedelta(seconds=1),
        WebhookDeliveryStatus.DELIVERED,
    )
    test_session.add_all([make_payment(), due, future, delivered])
    await test_session.commit()

    claimed_ids = await claim_due_webhook_events(
        test_session,
        CURRENT_TIME,
        lease_seconds=30,
    )

    assert claimed_ids == ["evt_due"]
    assert due.next_attempt_at == CURRENT_TIME + timedelta(seconds=30)
    assert future.next_attempt_at == CURRENT_TIME + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_worker_respects_claim_batch_size(test_session: AsyncSession):
    first = make_event("evt_first", CURRENT_TIME - timedelta(seconds=2))
    second = make_event("evt_second", CURRENT_TIME - timedelta(seconds=1))
    test_session.add_all([make_payment(), first, second])
    await test_session.commit()

    first_claim = await claim_due_webhook_events(
        test_session,
        CURRENT_TIME,
        batch_size=1,
    )
    second_claim = await claim_due_webhook_events(
        test_session,
        CURRENT_TIME,
        batch_size=1,
    )

    assert first_claim == ["evt_first"]
    assert second_claim == ["evt_second"]


@pytest.mark.asyncio
async def test_claimed_event_is_delivered_and_committed(
    test_session: AsyncSession,
):
    event = make_event("evt_send", CURRENT_TIME)
    test_session.add_all([make_payment(), event])
    await test_session.commit()
    await claim_due_webhook_events(test_session, CURRENT_TIME)

    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        delivered = await deliver_claimed_webhook_event(
            test_session,
            event.id,
            WEBHOOK_SECRET,
            client,
            attempted_at=CURRENT_TIME,
        )

    assert delivered is True
    assert event.status == WebhookDeliveryStatus.DELIVERED
    assert event.attempt_count == 1


@pytest.mark.asyncio
async def test_worker_rejects_invalid_claim_settings(
    test_session: AsyncSession,
):
    with pytest.raises(ValueError, match="must be positive"):
        await claim_due_webhook_events(test_session, CURRENT_TIME, batch_size=0)


@pytest.mark.asyncio
async def test_worker_rejects_timestamp_without_timezone(
    test_session: AsyncSession,
):
    with pytest.raises(ValueError, match="must include a timezone"):
        await claim_due_webhook_events(
            test_session,
            datetime(2026, 9, 1, 12, 0),
        )
