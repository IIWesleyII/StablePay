from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MerchantApiKey
from services.merchants import create_merchant_account


WALLET_ADDRESS = "0x2222222222222222222222222222222222222222"
WEBHOOK_URL = "https://merchant.test/webhooks/stablepay"


@pytest_asyncio.fixture
async def merchant_credentials(test_session: AsyncSession):
    created = await create_merchant_account(
        test_session,
        name="Authenticated Merchant",
        wallet_address=WALLET_ADDRESS,
        webhook_url=WEBHOOK_URL,
        api_key_name="Test key",
    )
    await test_session.commit()
    return created


def authorization_header(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


@pytest.mark.asyncio
async def test_valid_api_key_returns_current_merchant(
    client: AsyncClient,
    merchant_credentials,
):
    response = await client.get(
        "/merchants/me",
        headers=authorization_header(merchant_credentials.api_key),
    )

    assert response.status_code == 200
    assert response.json()["id"] == merchant_credentials.merchant.id
    assert response.json()["name"] == "Authenticated Merchant"
    assert response.json()["wallet_address"] == WALLET_ADDRESS


@pytest.mark.asyncio
async def test_valid_api_key_updates_last_used_time(
    client: AsyncClient,
    merchant_credentials,
    test_session: AsyncSession,
):
    api_key_record = await test_session.get(
        MerchantApiKey,
        merchant_credentials.api_key_id,
    )
    assert api_key_record.last_used_at is None

    response = await client.get(
        "/merchants/me",
        headers=authorization_header(merchant_credentials.api_key),
    )

    assert response.status_code == 200
    await test_session.refresh(api_key_record)
    assert api_key_record.last_used_at is not None


@pytest.mark.asyncio
async def test_missing_api_key_is_rejected(client: AsyncClient):
    response = await client.get("/merchants/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_malformed_api_key_is_rejected(client: AsyncClient):
    response = await client.get(
        "/merchants/me",
        headers=authorization_header("not-a-stablepay-key"),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key"


@pytest.mark.asyncio
async def test_wrong_secret_is_rejected(
    client: AsyncClient,
    merchant_credentials,
):
    key_parts = merchant_credentials.api_key.split(".")
    wrong_key = f"{key_parts[0]}.{key_parts[1]}.wrong-secret"

    response = await client.get(
        "/merchants/me",
        headers=authorization_header(wrong_key),
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_revoked_api_key_is_rejected(
    client: AsyncClient,
    merchant_credentials,
    test_session: AsyncSession,
):
    api_key_record = await test_session.get(
        MerchantApiKey,
        merchant_credentials.api_key_id,
    )
    api_key_record.revoked_at = datetime.now(timezone.utc)
    await test_session.commit()

    response = await client.get(
        "/merchants/me",
        headers=authorization_header(merchant_credentials.api_key),
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_expired_api_key_is_rejected(
    client: AsyncClient,
    merchant_credentials,
    test_session: AsyncSession,
):
    api_key_record = await test_session.get(
        MerchantApiKey,
        merchant_credentials.api_key_id,
    )
    api_key_record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await test_session.commit()

    response = await client.get(
        "/merchants/me",
        headers=authorization_header(merchant_credentials.api_key),
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_inactive_merchant_is_rejected(
    client: AsyncClient,
    merchant_credentials,
    test_session: AsyncSession,
):
    merchant_credentials.merchant.is_active = False
    await test_session.commit()

    response = await client.get(
        "/merchants/me",
        headers=authorization_header(merchant_credentials.api_key),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Merchant account is inactive"
