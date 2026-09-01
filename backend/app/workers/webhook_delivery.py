"""Background processing for durable merchant webhook events."""

import asyncio
import logging
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.database import SessionLocal
from database.models import WebhookEvent
from domain.webhooks import WebhookDeliveryStatus
from services.webhook_delivery import deliver_webhook_event


logger = logging.getLogger(__name__)


async def claim_due_webhook_events(
    session: AsyncSession,
    current_time: datetime | None = None,
    batch_size: int = 20,
    lease_seconds: int = 30,
) -> list[str]:
    """Lease one locked batch so multiple workers do not send it together."""

    if batch_size <= 0 or lease_seconds <= 0:
        raise ValueError("Webhook claim settings must be positive")

    claim_time = _as_utc(current_time or datetime.now(timezone.utc))
    lease_until = claim_time + timedelta(seconds=lease_seconds)

    result = await session.execute(
        select(WebhookEvent)
        .where(
            WebhookEvent.status == WebhookDeliveryStatus.PENDING,
            WebhookEvent.next_attempt_at <= claim_time,
        )
        .order_by(WebhookEvent.next_attempt_at, WebhookEvent.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    events = result.scalars().all()

    for event in events:
        event.next_attempt_at = lease_until

    await session.commit()
    return [event.id for event in events]


async def deliver_claimed_webhook_event(
    session: AsyncSession,
    event_id: str,
    secret: str,
    client: httpx.AsyncClient,
    attempted_at: datetime | None = None,
) -> bool | None:
    """Deliver one leased event and persist its resulting delivery state."""

    result = await session.execute(
        select(WebhookEvent).where(
            WebhookEvent.id == event_id,
            WebhookEvent.status == WebhookDeliveryStatus.PENDING,
        )
    )
    event = result.scalar_one_or_none()
    if event is None:
        await session.rollback()
        return None

    # The lease protects this event, so end the read transaction before making
    # a potentially slow network request to the merchant.
    await session.commit()

    delivered = await deliver_webhook_event(
        event,
        secret,
        client,
        attempted_at=attempted_at,
        max_attempts=settings.webhook_delivery_max_attempts,
        base_retry_seconds=settings.webhook_delivery_retry_seconds,
    )
    await session.commit()
    return delivered


async def run_webhook_delivery_worker(stop_event: asyncio.Event) -> None:
    """Continuously claim and deliver due webhooks until shutdown."""

    secret = settings.merchant_webhook_secret
    if secret is None:
        logger.warning(
            "Webhook delivery worker disabled because "
            "MERCHANT_WEBHOOK_SECRET is not configured"
        )
        return

    timeout = httpx.Timeout(settings.webhook_delivery_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while not stop_event.is_set():
            try:
                async with SessionLocal() as session:
                    event_ids = await claim_due_webhook_events(
                        session,
                        batch_size=settings.webhook_delivery_batch_size,
                        lease_seconds=settings.webhook_delivery_lease_seconds,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Webhook claim scan failed")
                event_ids = []

            if event_ids:
                await asyncio.gather(
                    *(
                        _deliver_event_safely(event_id, secret, client)
                        for event_id in event_ids
                    )
                )

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.webhook_delivery_poll_seconds,
                )
            except TimeoutError:
                pass


async def _deliver_event_safely(
    event_id: str,
    secret: str,
    client: httpx.AsyncClient,
) -> None:
    async with SessionLocal() as session:
        try:
            delivered = await deliver_claimed_webhook_event(
                session,
                event_id,
                secret,
                client,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await session.rollback()
            logger.exception("Webhook delivery failed for event %s", event_id)
            return

    if delivered is True:
        logger.info("Delivered webhook event %s", event_id)
    elif delivered is False:
        logger.warning("Webhook event %s scheduled for retry", event_id)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Webhook worker timestamps must include a timezone")
    return value.astimezone(timezone.utc)
