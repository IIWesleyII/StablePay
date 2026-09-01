from datetime import datetime
from datetime import timedelta
from datetime import timezone

import httpx
import pytest

from backend.fake_merchant import app
from backend.fake_merchant import received_events
from backend.fake_merchant import settings
from services.webhook_delivery import create_webhook_signature
from services.webhook_delivery import serialize_webhook_payload


WEBHOOK_SECRET = "test-webhook-secret-that-is-at-least-32-characters"
EVENT_ID = "evt_fake_merchant_test"
PAYLOAD = {
    "id": EVENT_ID,
    "type": "payment.confirmed",
    "data": {"payment": {"id": "pay_fake_merchant_test"}},
}


@pytest.fixture(autouse=True)
def reset_fake_merchant(monkeypatch):
    received_events.clear()
    monkeypatch.setattr(settings, "merchant_webhook_secret", WEBHOOK_SECRET)


def signed_headers(
    body: bytes,
    timestamp: str | None = None,
    event_id: str = EVENT_ID,
) -> dict[str, str]:
    timestamp = timestamp or str(int(datetime.now(timezone.utc).timestamp()))
    signature = create_webhook_signature(body, WEBHOOK_SECRET, timestamp)
    return {
        "Content-Type": "application/json",
        "StablePay-Event-Id": event_id,
        "StablePay-Timestamp": timestamp,
        "StablePay-Signature": f"v1={signature}",
    }


@pytest.mark.asyncio
async def test_fake_merchant_accepts_valid_signed_webhook():
    body = serialize_webhook_payload(PAYLOAD)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/stablepay",
            content=body,
            headers=signed_headers(body),
        )
        received_response = await client.get("/webhooks/received")

    assert response.status_code == 200
    assert response.json()["duplicate"] is False
    assert received_response.json()["count"] == 1
    assert received_response.json()["events"] == [PAYLOAD]


@pytest.mark.asyncio
async def test_fake_merchant_accepts_duplicate_without_processing_it_twice():
    body = serialize_webhook_payload(PAYLOAD)
    headers = signed_headers(body)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first_response = await client.post(
            "/webhooks/stablepay",
            content=body,
            headers=headers,
        )
        second_response = await client.post(
            "/webhooks/stablepay",
            content=body,
            headers=headers,
        )

    assert first_response.json()["duplicate"] is False
    assert second_response.status_code == 200
    assert second_response.json()["duplicate"] is True
    assert len(received_events) == 1


@pytest.mark.asyncio
async def test_fake_merchant_rejects_invalid_signature():
    body = serialize_webhook_payload(PAYLOAD)
    headers = signed_headers(body)
    headers["StablePay-Signature"] = "v1=" + "0" * 64
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/stablepay",
            content=body,
            headers=headers,
        )

    assert response.status_code == 401
    assert received_events == {}


@pytest.mark.asyncio
async def test_fake_merchant_rejects_stale_timestamp():
    body = serialize_webhook_payload(PAYLOAD)
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    timestamp = str(int(stale_time.timestamp()))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/stablepay",
            content=body,
            headers=signed_headers(body, timestamp=timestamp),
        )

    assert response.status_code == 400
    assert "outside the allowed window" in response.json()["detail"]


@pytest.mark.asyncio
async def test_fake_merchant_rejects_header_and_body_id_mismatch():
    body = serialize_webhook_payload(PAYLOAD)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/stablepay",
            content=body,
            headers=signed_headers(body, event_id="evt_different"),
        )

    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]
