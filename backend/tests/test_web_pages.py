from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Merchant
from database.models import Payment
from domain.payments import PaymentStatus


@pytest.mark.asyncio
async def test_dashboard_page_is_public_shell_without_merchant_data(
    client: AsyncClient,
):
    response = await client.get("/dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "StablePay demo console" in response.text
    assert "StablePay API key" in response.text
    assert "One deposit. Many tiny payments. One settlement." in response.text
    assert "Pay for a simulated API call" in response.text
    assert "Customer vault token" in response.text
    assert "Merchant USDC balance" in response.text
    assert "Request settlement" in response.text
    assert "/static/dashboard.js" in response.text
    assert "mch_authenticated_test" not in response.text


@pytest.mark.asyncio
async def test_root_redirects_to_demo_console(client: AsyncClient):
    response = await client.get("/", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"


@pytest.mark.asyncio
async def test_dashboard_static_assets_are_served(client: AsyncClient):
    css_response = await client.get("/static/styles.css")
    javascript_response = await client.get("/static/dashboard.js")
    favicon_response = await client.get("/static/favicon.svg")

    assert css_response.status_code == 200
    assert css_response.headers["content-type"].startswith("text/css")
    assert "--brand:" in css_response.text
    assert javascript_response.status_code == 200
    assert "sessionStorage" in javascript_response.text
    assert "stablepay_vault_token" in javascript_response.text
    assert 'vaultRequest("/vaults/micropayments"' in javascript_response.text
    assert "/merchants/me/settlements" in javascript_response.text
    assert favicon_response.status_code == 200
    assert favicon_response.headers["content-type"].startswith("image/svg+xml")


@pytest.mark.asyncio
async def test_checkout_page_exists_for_a_real_payment(
    authenticated_client: AsyncClient,
):
    create_response = await authenticated_client.post(
        "/payments",
        json={"amount": "0.01"},
    )
    payment_id = create_response.json()["id"]

    response = await authenticated_client.get(f"/checkout/{payment_id}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert f'data-payment-id="{payment_id}"' in response.text
    assert "Send on Base Sepolia" in response.text
    assert "/static/checkout.js" in response.text


@pytest.mark.asyncio
async def test_checkout_page_rejects_unknown_payment(client: AsyncClient):
    response = await client.get("/checkout/pay_does_not_exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}


@pytest.mark.asyncio
async def test_public_checkout_status_contains_only_customer_fields(
    authenticated_client: AsyncClient,
    authenticated_merchant: Merchant,
):
    create_response = await authenticated_client.post(
        "/payments",
        json={"amount": "0.001"},
    )
    payment = create_response.json()

    response = await authenticated_client.get(
        f"/checkout/{payment['id']}/status"
    )

    assert response.status_code == 200
    checkout = response.json()
    assert set(checkout) == {
        "id",
        "merchant_name",
        "amount",
        "currency",
        "chain",
        "recipient_address",
        "status",
        "transaction_hash",
        "expires_at",
        "confirmed_at",
    }
    assert checkout["merchant_name"] == authenticated_merchant.name
    assert Decimal(checkout["amount"]) == Decimal("0.001")
    assert checkout["recipient_address"] == authenticated_merchant.wallet_address
    assert "merchant_id" not in checkout
    assert "webhook_url" not in checkout
    assert "api_key" not in checkout


@pytest.mark.asyncio
async def test_checkout_status_supports_legacy_payment_without_merchant(
    client: AsyncClient,
    test_session: AsyncSession,
):
    current_time = datetime.now(timezone.utc)
    payment = Payment(
        id="pay_legacy_checkout_test",
        merchant_id=None,
        amount=Decimal("1.00"),
        currency="USDC",
        chain="base-sepolia",
        recipient_address="0x7777777777777777777777777777777777777777",
        status=PaymentStatus.PENDING,
        created_at=current_time,
        expires_at=current_time + timedelta(minutes=15),
    )
    test_session.add(payment)
    await test_session.commit()

    response = await client.get(f"/checkout/{payment.id}/status")

    assert response.status_code == 200
    assert response.json()["merchant_name"] == "StablePay Merchant"


@pytest.mark.asyncio
async def test_checkout_status_rejects_unknown_payment(client: AsyncClient):
    response = await client.get(
        "/checkout/pay_does_not_exist/status"
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}
