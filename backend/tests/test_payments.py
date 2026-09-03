from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.authentication import get_authenticated_merchant
from config import settings
from database.models import Merchant
from database.models import Payment
from domain.payments import PaymentStatus
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


@pytest.mark.asyncio
async def test_list_payments_returns_recent_payments_and_status_counts(
    authenticated_client: AsyncClient,
    test_session: AsyncSession,
):
    first_response = await authenticated_client.post(
        "/payments",
        json={"amount": "1.00"},
    )
    second_response = await authenticated_client.post(
        "/payments",
        json={"amount": "2.00"},
    )
    third_response = await authenticated_client.post(
        "/payments",
        json={"amount": "3.00"},
    )

    second_payment = await test_session.get(
        Payment,
        second_response.json()["id"],
    )
    third_payment = await test_session.get(
        Payment,
        third_response.json()["id"],
    )
    second_payment.status = PaymentStatus.CONFIRMED
    second_payment.detected_at = datetime.now(timezone.utc)
    second_payment.confirmed_at = datetime.now(timezone.utc)
    third_payment.status = PaymentStatus.EXPIRED
    await test_session.commit()

    response = await authenticated_client.get("/payments?limit=2")

    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 3
    assert result["limit"] == 2
    assert result["offset"] == 0
    assert len(result["items"]) == 2
    assert result["items"][0]["id"] == third_response.json()["id"]
    assert result["items"][1]["id"] == second_response.json()["id"]
    assert result["status_counts"] == {
        "pending": 1,
        "confirming": 0,
        "confirmed": 1,
        "expired": 1,
    }
    assert first_response.status_code == 201


@pytest.mark.asyncio
async def test_list_payments_can_filter_and_paginate(
    authenticated_client: AsyncClient,
):
    first_response = await authenticated_client.post(
        "/payments",
        json={"amount": "1.00"},
    )
    second_response = await authenticated_client.post(
        "/payments",
        json={"amount": "2.00"},
    )

    response = await authenticated_client.get(
        "/payments?status=pending&limit=1&offset=1"
    )

    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 2
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == first_response.json()["id"]
    assert result["items"][0]["id"] != second_response.json()["id"]


@pytest.mark.asyncio
async def test_payment_list_is_isolated_between_merchants(
    authenticated_client: AsyncClient,
    authenticated_merchant: Merchant,
    test_session: AsyncSession,
):
    own_response = await authenticated_client.post(
        "/payments",
        json={"amount": "1.00"},
    )
    current_time = datetime.now(timezone.utc)
    other_merchant = Merchant(
        id="mch_other_list_test",
        name="Other List Merchant",
        wallet_address="0x4444444444444444444444444444444444444444",
        webhook_url="https://other-list.test/webhook",
        created_at=current_time,
        updated_at=current_time,
    )
    test_session.add(other_merchant)
    await test_session.flush()

    test_session.add(
        Payment(
            id="pay_other_merchant_list_test",
            merchant_id=other_merchant.id,
            amount="9.00",
            currency="USDC",
            chain="base-sepolia",
            recipient_address=other_merchant.wallet_address,
            status=PaymentStatus.PENDING,
            created_at=current_time,
            expires_at=current_time + timedelta(minutes=15),
        )
    )
    await test_session.commit()

    response = await authenticated_client.get("/payments")

    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 1
    assert [item["id"] for item in result["items"]] == [
        own_response.json()["id"]
    ]
    assert result["status_counts"]["pending"] == 1


@pytest.mark.asyncio
async def test_payment_list_requires_authentication(client: AsyncClient):
    response = await client.get("/payments")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_payment_list_rejects_invalid_pagination(
    authenticated_client: AsyncClient,
):
    response = await authenticated_client.get("/payments?limit=101&offset=-1")

    assert response.status_code == 422
