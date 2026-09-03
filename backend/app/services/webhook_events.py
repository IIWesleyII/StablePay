"""Create durable webhook events without committing the database session."""

from datetime import datetime
from datetime import timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.models import Merchant
from database.models import Payment
from database.models import WebhookEvent
from domain.payments import PaymentStatus
from domain.webhooks import WebhookDeliveryStatus


PAYMENT_CONFIRMED_EVENT = "payment.confirmed"


class WebhookEventError(ValueError):
    """Raised when a webhook event cannot be safely created."""


async def get_payment_webhook_url(
    payment: Payment,
    session: AsyncSession,
) -> str:
    """Use merchant configuration while retaining legacy-payment behavior."""

    if payment.merchant_id is None:
        return settings.merchant_webhook_url

    merchant = await session.get(Merchant, payment.merchant_id)
    if merchant is None:
        raise WebhookEventError("Payment merchant account no longer exists")

    return merchant.webhook_url


def create_payment_confirmed_event(
    payment: Payment,
    destination_url: str,
    created_at: datetime | None = None,
) -> WebhookEvent:
    """Build one durable event for a newly confirmed payment."""

    if payment.status != PaymentStatus.CONFIRMED:
        raise WebhookEventError(
            "A payment.confirmed event requires a confirmed payment"
        )

    if payment.transaction_hash is None or payment.confirmed_at is None:
        raise WebhookEventError(
            "A confirmed payment must include its transaction and confirmation time"
        )

    event_time = _as_utc(created_at or datetime.now(timezone.utc))
    confirmation_time = _as_utc(payment.confirmed_at)
    event_id = f"evt_{uuid4().hex}"

    payload = {
        "id": event_id,
        "type": PAYMENT_CONFIRMED_EVENT,
        "created_at": event_time.isoformat(),
        "data": {
            "payment": {
                "id": payment.id,
                "merchant_id": payment.merchant_id,
                "amount": format(payment.amount, "f"),
                "currency": payment.currency,
                "chain": payment.chain,
                "recipient_address": payment.recipient_address,
                "status": payment.status.value,
                "transaction_hash": payment.transaction_hash,
                "payer_address": payment.payer_address,
                "transaction_block_number": payment.transaction_block_number,
                "transaction_log_index": payment.transaction_log_index,
                "confirmed_at": confirmation_time.isoformat(),
            }
        },
    }

    return WebhookEvent(
        id=event_id,
        event_type=PAYMENT_CONFIRMED_EVENT,
        payment_id=payment.id,
        destination_url=destination_url,
        payload=payload,
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=0,
        next_attempt_at=event_time,
        created_at=event_time,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WebhookEventError("Webhook event timestamps must include a timezone")
    return value.astimezone(timezone.utc)
