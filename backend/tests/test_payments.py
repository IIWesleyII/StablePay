from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient

from config import settings


@pytest.mark.asyncio
async def test_create_payment(client: AsyncClient):
    response = await client.post("/payments", json={"amount": "1.25"})

    assert response.status_code == 201

    payment = response.json()
    assert payment["id"].startswith("pay_")
    assert Decimal(payment["amount"]) == Decimal("1.25")
    assert payment["currency"] == "USDC"
    assert payment["chain"] == "base-sepolia"
    assert payment["recipient_address"] == settings.merchant_wallet_address
    assert payment["status"] == "pending"
    assert payment["transaction_hash"] is None
    assert payment["created_at"] is not None
    assert payment["expires_at"] is not None
    assert payment["detected_at"] is None
    assert payment["confirmed_at"] is None


@pytest.mark.asyncio
async def test_payment_uses_configured_expiration_window(client: AsyncClient):
    response = await client.post("/payments", json={"amount": "1.00"})
    payment = response.json()

    created_at = datetime.fromisoformat(payment["created_at"])
    expires_at = datetime.fromisoformat(payment["expires_at"])

    assert expires_at - created_at == timedelta(
        minutes=settings.payment_expiration_minutes
    )


@pytest.mark.asyncio
async def test_get_existing_payment(client: AsyncClient):
    create_response = await client.post("/payments", json={"amount": "2.50"})
    payment_id = create_response.json()["id"]

    response = await client.get(f"/payments/{payment_id}")

    assert response.status_code == 200
    assert response.json()["id"] == payment_id
    assert Decimal(response.json()["amount"]) == Decimal("2.50")


@pytest.mark.asyncio
async def test_get_unknown_payment_returns_not_found(client: AsyncClient):
    response = await client.get("/payments/pay_does_not_exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}


@pytest.mark.asyncio
async def test_zero_amount_is_rejected(client: AsyncClient):
    response = await client.post("/payments", json={"amount": "0"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_negative_amount_is_rejected(client: AsyncClient):
    response = await client.post("/payments", json={"amount": "-1"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_more_than_six_decimal_places_is_rejected(client: AsyncClient):
    response = await client.post("/payments", json={"amount": "0.0000001"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_smallest_usdc_unit_is_accepted(client: AsyncClient):
    response = await client.post("/payments", json={"amount": "0.000001"})

    assert response.status_code == 201
    assert Decimal(response.json()["amount"]) == Decimal("0.000001")
