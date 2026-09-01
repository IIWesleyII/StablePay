import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from database.models import Merchant
from services.merchants import create_merchant_account


ORIGINAL_WALLET = "0x5555555555555555555555555555555555555555"
UPDATED_WALLET = "0x6666666666666666666666666666666666666666"
WEBHOOK_URL = "https://account-management.test/webhooks/stablepay"


@pytest_asyncio.fixture
async def account_management_merchant(test_session: AsyncSession):
    created = await create_merchant_account(
        test_session,
        name="Original Merchant",
        wallet_address=ORIGINAL_WALLET,
        webhook_url=WEBHOOK_URL,
        api_key_name="Account management",
    )
    await test_session.commit()
    return created


def authorization_header(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


@pytest.mark.asyncio
async def test_merchant_can_update_account_settings(
    client: AsyncClient,
    test_session: AsyncSession,
    account_management_merchant,
):
    response = await client.patch(
        "/merchants/me",
        headers=authorization_header(account_management_merchant.api_key),
        json={
            "name": "  Updated Merchant  ",
            "wallet_address": UPDATED_WALLET.lower(),
            "webhook_url": "  https://updated.test/stablepay  ",
        },
    )

    assert response.status_code == 200
    merchant_data = response.json()
    assert merchant_data["name"] == "Updated Merchant"
    assert merchant_data["wallet_address"] == Web3.to_checksum_address(
        UPDATED_WALLET
    )
    assert merchant_data["webhook_url"] == "https://updated.test/stablepay"

    stored_merchant = await test_session.get(
        Merchant,
        account_management_merchant.merchant.id,
    )
    await test_session.refresh(stored_merchant)
    assert stored_merchant.name == "Updated Merchant"
    assert stored_merchant.wallet_address == Web3.to_checksum_address(
        UPDATED_WALLET
    )


@pytest.mark.asyncio
async def test_wallet_change_only_affects_future_payments(
    client: AsyncClient,
    account_management_merchant,
):
    headers = authorization_header(account_management_merchant.api_key)
    first_payment_response = await client.post(
        "/payments",
        headers=headers,
        json={"amount": "0.01"},
    )
    assert first_payment_response.status_code == 201

    update_response = await client.patch(
        "/merchants/me",
        headers=headers,
        json={"wallet_address": UPDATED_WALLET},
    )
    assert update_response.status_code == 200

    second_payment_response = await client.post(
        "/payments",
        headers=headers,
        json={"amount": "0.02"},
    )
    assert second_payment_response.status_code == 201

    assert first_payment_response.json()["recipient_address"] == (
        Web3.to_checksum_address(ORIGINAL_WALLET)
    )
    assert second_payment_response.json()["recipient_address"] == (
        Web3.to_checksum_address(UPDATED_WALLET)
    )


@pytest.mark.asyncio
async def test_merchant_cannot_take_another_merchants_wallet(
    client: AsyncClient,
    test_session: AsyncSession,
    account_management_merchant,
):
    other = await create_merchant_account(
        test_session,
        name="Other Merchant",
        wallet_address=UPDATED_WALLET,
        webhook_url="https://other-account.test/webhooks/stablepay",
    )
    await test_session.commit()

    response = await client.patch(
        "/merchants/me",
        headers=authorization_header(account_management_merchant.api_key),
        json={"wallet_address": other.merchant.wallet_address.lower()},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "A merchant already exists for this wallet address"
    )


@pytest.mark.parametrize(
    "request_body",
    [
        {},
        {"name": None},
        {"name": "   "},
        {"wallet_address": "not-a-wallet"},
        {"webhook_url": "not-a-url"},
    ],
)
@pytest.mark.asyncio
async def test_invalid_account_updates_are_rejected(
    client: AsyncClient,
    account_management_merchant,
    request_body: dict,
):
    response = await client.patch(
        "/merchants/me",
        headers=authorization_header(account_management_merchant.api_key),
        json=request_body,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_account_update_requires_authentication(client: AsyncClient):
    response = await client.patch(
        "/merchants/me",
        json={"name": "Unauthorized update"},
    )

    assert response.status_code == 401
