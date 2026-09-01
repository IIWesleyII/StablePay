from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest

from database.models import Payment
from domain.payments import PaymentStatus
from services.webhook_events import PAYMENT_CONFIRMED_EVENT
from services.webhook_events import WebhookEventError
from services.webhook_events import create_payment_confirmed_event


def make_confirmed_payment() -> Payment:
    created_at = datetime.now(timezone.utc)
    return Payment(
        id="pay_confirmed_event",
        amount=Decimal("0.010000"),
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


def test_confirmed_payment_event_contains_delivery_snapshot():
    payment = make_confirmed_payment()

    event = create_payment_confirmed_event(
        payment,
        "http://merchant.test/webhooks/stablepay",
    )

    assert event.id.startswith("evt_")
    assert event.event_type == PAYMENT_CONFIRMED_EVENT
    assert event.payment_id == payment.id
    assert event.payload["id"] == event.id
    assert event.payload["type"] == "payment.confirmed"
    assert event.payload["data"]["payment"]["amount"] == "0.010000"
    assert event.payload["data"]["payment"]["status"] == "confirmed"
    assert event.payload["data"]["payment"]["transaction_hash"] == (
        payment.transaction_hash
    )


def test_unconfirmed_payment_event_is_rejected():
    payment = make_confirmed_payment()
    payment.status = PaymentStatus.CONFIRMING

    with pytest.raises(WebhookEventError, match="requires a confirmed payment"):
        create_payment_confirmed_event(
            payment,
            "http://merchant.test/webhooks/stablepay",
        )
