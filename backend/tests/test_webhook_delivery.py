import hashlib
import hmac
from datetime import datetime
from datetime import timezone

import httpx
import pytest

from database.models import WebhookEvent
from domain.webhooks import WebhookDeliveryStatus
from services.webhook_delivery import WebhookDeliveryError
from services.webhook_delivery import deliver_webhook_event


WEBHOOK_SECRET = "test-webhook-secret-that-is-at-least-32-characters"
ATTEMPT_TIME = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def make_event(attempt_count: int = 0) -> WebhookEvent:
    return WebhookEvent(
        id="evt_delivery_test",
        event_type="payment.confirmed",
        payment_id="pay_delivery_test",
        destination_url="https://merchant.test/webhooks/stablepay",
        payload={
            "id": "evt_delivery_test",
            "type": "payment.confirmed",
            "data": {"payment": {"id": "pay_delivery_test"}},
        },
        status=WebhookDeliveryStatus.PENDING,
        attempt_count=attempt_count,
        next_attempt_at=ATTEMPT_TIME,
        created_at=ATTEMPT_TIME,
    )


@pytest.mark.asyncio
async def test_successful_delivery_is_signed_and_marked_delivered():
    captured_request = None

    def merchant_receiver(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(204)

    event = make_event()
    transport = httpx.MockTransport(merchant_receiver)

    async with httpx.AsyncClient(transport=transport) as client:
        delivered = await deliver_webhook_event(
            event,
            WEBHOOK_SECRET,
            client,
            attempted_at=ATTEMPT_TIME,
        )

    assert delivered is True
    assert captured_request is not None
    timestamp = captured_request.headers["StablePay-Timestamp"]
    expected_signature = hmac.new(
        WEBHOOK_SECRET.encode(),
        timestamp.encode() + b"." + captured_request.content,
        hashlib.sha256,
    ).hexdigest()
    assert captured_request.headers["StablePay-Signature"] == (
        f"v1={expected_signature}"
    )
    assert captured_request.headers["StablePay-Event-Id"] == event.id
    assert event.status == WebhookDeliveryStatus.DELIVERED
    assert event.attempt_count == 1
    assert event.delivered_at == ATTEMPT_TIME
    assert event.last_error is None


@pytest.mark.asyncio
async def test_failed_delivery_schedules_exponential_retry():
    def unavailable_merchant(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="sensitive merchant response")

    event = make_event()
    transport = httpx.MockTransport(unavailable_merchant)

    async with httpx.AsyncClient(transport=transport) as client:
        delivered = await deliver_webhook_event(
            event,
            WEBHOOK_SECRET,
            client,
            attempted_at=ATTEMPT_TIME,
            base_retry_seconds=5,
        )

    assert delivered is False
    assert event.status == WebhookDeliveryStatus.PENDING
    assert event.attempt_count == 1
    assert (event.next_attempt_at - ATTEMPT_TIME).total_seconds() == 5
    assert event.last_error == "Merchant returned HTTP 503"
    assert "sensitive" not in event.last_error


@pytest.mark.asyncio
async def test_last_failed_attempt_stops_retrying():
    def unavailable_merchant(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    event = make_event(attempt_count=4)
    transport = httpx.MockTransport(unavailable_merchant)

    async with httpx.AsyncClient(transport=transport) as client:
        delivered = await deliver_webhook_event(
            event,
            WEBHOOK_SECRET,
            client,
            attempted_at=ATTEMPT_TIME,
            max_attempts=5,
        )

    assert delivered is False
    assert event.status == WebhookDeliveryStatus.FAILED
    assert event.attempt_count == 5
    assert event.next_attempt_at == ATTEMPT_TIME


@pytest.mark.asyncio
async def test_network_error_is_recorded_without_internal_details():
    def disconnected_merchant(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "private network details that should not be stored",
            request=request,
        )

    event = make_event()
    transport = httpx.MockTransport(disconnected_merchant)

    async with httpx.AsyncClient(transport=transport) as client:
        delivered = await deliver_webhook_event(
            event,
            WEBHOOK_SECRET,
            client,
            attempted_at=ATTEMPT_TIME,
        )

    assert delivered is False
    assert event.status == WebhookDeliveryStatus.PENDING
    assert event.last_error == "Network error while delivering webhook"
    assert "private network" not in event.last_error


@pytest.mark.asyncio
async def test_delivered_event_cannot_be_sent_again():
    event = make_event()
    event.status = WebhookDeliveryStatus.DELIVERED

    async with httpx.AsyncClient() as client:
        with pytest.raises(WebhookDeliveryError, match="status delivered"):
            await deliver_webhook_event(event, WEBHOOK_SECRET, client)
