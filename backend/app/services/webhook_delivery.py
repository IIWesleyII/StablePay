"""Sign and attempt delivery of durable webhook events."""

import hashlib
import hmac
import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone

import httpx

from database.models import WebhookEvent
from domain.webhooks import WebhookDeliveryStatus


SIGNATURE_VERSION = "v1"


class WebhookDeliveryError(ValueError):
    """Raised when an event cannot be safely delivered."""


async def deliver_webhook_event(
    event: WebhookEvent,
    secret: str,
    client: httpx.AsyncClient,
    attempted_at: datetime | None = None,
    max_attempts: int = 5,
    base_retry_seconds: int = 5,
) -> bool:
    """Attempt one delivery and update the event without committing it."""

    if event.status != WebhookDeliveryStatus.PENDING:
        raise WebhookDeliveryError(
            f"Cannot deliver a webhook with status {event.status.value}"
        )
    if len(secret) < 32:
        raise WebhookDeliveryError(
            "Webhook signing secret must contain at least 32 characters"
        )
    if max_attempts <= 0 or base_retry_seconds <= 0:
        raise WebhookDeliveryError("Retry settings must be positive")

    attempt_time = _as_utc(attempted_at or datetime.now(timezone.utc))
    timestamp = str(int(attempt_time.timestamp()))
    body = serialize_webhook_payload(event.payload)
    signature = create_webhook_signature(body, secret, timestamp)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "StablePay-Webhooks/1.0",
        "StablePay-Event-Id": event.id,
        "StablePay-Timestamp": timestamp,
        "StablePay-Signature": f"{SIGNATURE_VERSION}={signature}",
    }

    failure_message: str | None = None

    try:
        response = await client.post(
            event.destination_url,
            content=body,
            headers=headers,
        )
        status_code = response.status_code
        if not 200 <= status_code < 300:
            failure_message = f"Merchant returned HTTP {status_code}"
    except httpx.RequestError:
        failure_message = "Network error while delivering webhook"

    event.attempt_count += 1

    if failure_message is None:
        event.status = WebhookDeliveryStatus.DELIVERED
        event.delivered_at = attempt_time
        event.next_attempt_at = attempt_time
        event.last_error = None
        return True

    event.last_error = failure_message
    event.delivered_at = None

    if event.attempt_count >= max_attempts:
        event.status = WebhookDeliveryStatus.FAILED
        event.next_attempt_at = attempt_time
    else:
        retry_delay = base_retry_seconds * (2 ** (event.attempt_count - 1))
        event.status = WebhookDeliveryStatus.PENDING
        event.next_attempt_at = attempt_time + timedelta(seconds=retry_delay)

    return False


def serialize_webhook_payload(payload: dict) -> bytes:
    """Produce stable JSON bytes so senders and receivers sign the same body."""

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def create_webhook_signature(body: bytes, secret: str, timestamp: str) -> str:
    """Create an HMAC-SHA256 signature covering time and exact request body."""

    signed_content = timestamp.encode("utf-8") + b"." + body
    return hmac.new(
        secret.encode("utf-8"),
        signed_content,
        hashlib.sha256,
    ).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WebhookDeliveryError(
            "Webhook delivery timestamps must include a timezone"
        )
    return value.astimezone(timezone.utc)
