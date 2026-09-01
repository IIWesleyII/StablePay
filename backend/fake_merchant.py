"""Local-only merchant server for manually testing StablePay webhooks."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime
from datetime import timezone
from pathlib import Path
import sys
from typing import Any

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request


APP_DIRECTORY = Path(__file__).resolve().parent / "app"
sys.path.insert(0, str(APP_DIRECTORY))

from config import settings  # noqa: E402


logger = logging.getLogger(__name__)

SIGNATURE_VERSION = "v1"
SIGNATURE_TOLERANCE_SECONDS = 300

received_events: dict[str, dict[str, Any]] = {}
received_events_lock = asyncio.Lock()

app = FastAPI(
    title="StablePay Fake Merchant",
    description="Local test receiver; do not deploy this application publicly.",
)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/stablepay")
async def receive_stablepay_webhook(request: Request) -> dict[str, Any]:
    """Verify, deduplicate, and record one StablePay webhook."""

    secret = settings.merchant_webhook_secret
    if secret is None:
        raise HTTPException(
            status_code=503,
            detail="MERCHANT_WEBHOOK_SECRET is not configured",
        )

    event_id = request.headers.get("StablePay-Event-Id")
    timestamp = request.headers.get("StablePay-Timestamp")
    supplied_signature = request.headers.get("StablePay-Signature")

    if event_id is None or timestamp is None or supplied_signature is None:
        raise HTTPException(
            status_code=400,
            detail="Required StablePay webhook headers are missing",
        )

    _verify_timestamp(timestamp)
    body = await request.body()
    _verify_signature(body, timestamp, supplied_signature, secret)

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=400, detail="Webhook body is not valid JSON") from error

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook body must be a JSON object")
    if payload.get("id") != event_id:
        raise HTTPException(
            status_code=400,
            detail="Webhook event ID does not match its signed body",
        )

    async with received_events_lock:
        if event_id in received_events:
            return {
                "received": True,
                "duplicate": True,
                "event_id": event_id,
            }

        received_events[event_id] = payload

    payment = payload.get("data", {}).get("payment", {})
    logger.info(
        "Received %s event %s for payment %s",
        payload.get("type"),
        event_id,
        payment.get("id"),
    )

    return {
        "received": True,
        "duplicate": False,
        "event_id": event_id,
    }


@app.get("/webhooks/received")
async def list_received_webhooks() -> dict[str, Any]:
    """Show events accepted since this local server started."""

    async with received_events_lock:
        events = list(received_events.values())

    return {
        "count": len(events),
        "events": events,
    }


def _verify_timestamp(timestamp: str) -> None:
    try:
        timestamp_value = int(timestamp)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="StablePay timestamp must be an integer",
        ) from error

    current_timestamp = int(datetime.now(timezone.utc).timestamp())
    if abs(current_timestamp - timestamp_value) > SIGNATURE_TOLERANCE_SECONDS:
        raise HTTPException(
            status_code=400,
            detail="StablePay webhook timestamp is outside the allowed window",
        )


def _verify_signature(
    body: bytes,
    timestamp: str,
    supplied_signature: str,
    secret: str,
) -> None:
    prefix = f"{SIGNATURE_VERSION}="
    if not supplied_signature.startswith(prefix):
        raise HTTPException(status_code=401, detail="Webhook signature is invalid")

    signed_content = timestamp.encode("utf-8") + b"." + body
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        signed_content,
        hashlib.sha256,
    ).hexdigest()
    received_signature = supplied_signature[len(prefix) :]

    if not hmac.compare_digest(expected_signature, received_signature):
        raise HTTPException(status_code=401, detail="Webhook signature is invalid")
