from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.authentication import get_authenticated_merchant
from config import settings
from database.models import Merchant
from main import app


@pytest.mark.asyncio
async def test_create_payment(
    authenticated_client: AsyncClient,
    authenticated_merchant: Merchant,
):
    response = await authenticated_client.post(
        "/payments",
        json={"amount": "1.25"},
    )

    assert response.status_code == 201

    payment = response.json()
    assert payment["id"].startswith("pay_")
    assert payment["merchant_id"] == authenticated_merchant.id
    assert Decimal(payment["amount"]) == Decimal("1.25")
    assert payment["currency"] == "USDC"
    assert payment["chain"] == "base-sepolia"
    assert payment["recipient_address"] == authenticated_merchant.wallet_address
    assert payment["status"] == "pending"
    assert payment["transaction_hash"] is None
    assert payment["created_at"] is not None
    assert payment["expires_at"] is not None
    assert payment["detected_at"] is None
    assert payment["confirmed_at"] is None


@pytest.mark.asyncio
async def test_payment_uses_configured_expiration_window(
    authenticated_client: AsyncClient,
):
    response = await authenticated_client.post(
        "/payments",
        json={"amount": "1.00"},
    )
    payment = response.json()

    created_at = datetime.fromisoformat(payment["created_at"])
    expires_at = datetime.fromisoformat(payment["expires_at"])

    assert expires_at - created_at == timedelta(
        minutes=settings.payment_expiration_minutes
    )


@pytest.mark.asyncio
async def test_get_existing_payment(authenticated_client: AsyncClient):
    create_response = await authenticated_client.post(
        "/payments",
        json={"amount": "2.50"},
    )
    payment_id = create_response.json()["id"]

    response = await authenticated_client.get(f"/payments/{payment_id}")

    assert response.status_code == 200
    assert response.json()["id"] == payment_id
    assert Decimal(response.json()["amount"]) == Decimal("2.50")


@pytest.mark.asyncio
async def test_get_unknown_payment_returns_not_found(
    authenticated_client: AsyncClient,
):
    response = await authenticated_client.get("/payments/pay_does_not_exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}


@pytest.mark.asyncio
async def test_zero_amount_is_rejected(authenticated_client: AsyncClient):
    response = await authenticated_client.post(
        "/payments",
        json={"amount": "0"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_negative_amount_is_rejected(authenticated_client: AsyncClient):
    response = await authenticated_client.post(
        "/payments",
        json={"amount": "-1"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_more_than_six_decimal_places_is_rejected(
    authenticated_client: AsyncClient,
):
    response = await authenticated_client.post(
        "/payments",
        json={"amount": "0.0000001"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_smallest_usdc_unit_is_accepted(
    authenticated_client: AsyncClient,
):
    response = await authenticated_client.post(
        "/payments",
        json={"amount": "0.000001"},
    )

    assert response.status_code == 201
    assert Decimal(response.json()["amount"]) == Decimal("0.000001")


@pytest.mark.asyncio
async def test_create_payment_requires_authentication(client: AsyncClient):
    response = await client.post("/payments", json={"amount": "1.00"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_merchant_cannot_get_another_merchants_payment(
    authenticated_client: AsyncClient,
    authenticated_merchant: Merchant,
    test_session: AsyncSession,
):
    create_response = await authenticated_client.post(
        "/payments",
        json={"amount": "1.00"},
    )
    payment_id = create_response.json()["id"]
    current_time = datetime.now(authenticated_merchant.created_at.tzinfo)
    other_merchant = Merchant(
        id="mch_other_test",
        name="Other Merchant",
        wallet_address="0x3333333333333333333333333333333333333333",
        webhook_url="https://other.test/webhook",
        created_at=current_time,
        updated_at=current_time,
    )
    test_session.add(other_merchant)
    await test_session.commit()
    app.dependency_overrides[get_authenticated_merchant] = lambda: other_merchant

    response = await authenticated_client.get(f"/payments/{payment_id}")

    assert response.status_code == 404
