from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MerchantApiKey
from services.api_keys import parse_api_key_id
from services.api_keys import verify_api_key
from services.merchants import create_merchant_account


WALLET_ADDRESS = "0x3333333333333333333333333333333333333333"
WEBHOOK_URL = "https://key-management.test/webhooks/stablepay"


@pytest_asyncio.fixture
async def key_management_merchant(test_session: AsyncSession):
    created = await create_merchant_account(
        test_session,
        name="Key Management Merchant",
        wallet_address=WALLET_ADDRESS,
        webhook_url=WEBHOOK_URL,
        api_key_name="Initial key",
    )
    await test_session.commit()
    return created


def authorization_header(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


@pytest.mark.asyncio
async def test_merchant_can_create_another_api_key(
    client: AsyncClient,
    test_session: AsyncSession,
    key_management_merchant,
):
    response = await client.post(
        "/merchants/me/api-keys",
        headers=authorization_header(key_management_merchant.api_key),
        json={"name": "  Production server  "},
    )

    assert response.status_code == 201
    response_data = response.json()
    assert response_data["name"] == "Production server"
    assert response_data["api_key"].startswith("sp_test.key_")
    assert response_data["id"] == parse_api_key_id(response_data["api_key"])
    assert "secret_hash" not in response_data

    stored_key = await test_session.get(MerchantApiKey, response_data["id"])
    assert stored_key is not None
    assert stored_key.merchant_id == key_management_merchant.merchant.id
    assert stored_key.secret_hash != response_data["api_key"]
    assert verify_api_key(response_data["api_key"], stored_key.secret_hash)


@pytest.mark.asyncio
async def test_key_list_contains_metadata_but_no_secrets(
    client: AsyncClient,
    key_management_merchant,
):
    create_response = await client.post(
        "/merchants/me/api-keys",
        headers=authorization_header(key_management_merchant.api_key),
        json={"name": "Second key"},
    )
    assert create_response.status_code == 201

    response = await client.get(
        "/merchants/me/api-keys",
        headers=authorization_header(key_management_merchant.api_key),
    )

    assert response.status_code == 200
    keys = response.json()
    assert len(keys) == 2
    assert {key["name"] for key in keys} == {"Initial key", "Second key"}
    assert all("api_key" not in key for key in keys)
    assert all("secret_hash" not in key for key in keys)
    assert all(key["key_prefix"].startswith("sp_test.key_") for key in keys)


@pytest.mark.asyncio
async def test_revoked_key_can_no_longer_authenticate(
    client: AsyncClient,
    test_session: AsyncSession,
    key_management_merchant,
):
    create_response = await client.post(
        "/merchants/me/api-keys",
        headers=authorization_header(key_management_merchant.api_key),
        json={"name": "Temporary key"},
    )
    new_key = create_response.json()

    response = await client.delete(
        f"/merchants/me/api-keys/{new_key['id']}",
        headers=authorization_header(key_management_merchant.api_key),
    )

    assert response.status_code == 204
    assert response.content == b""
    stored_key = await test_session.get(MerchantApiKey, new_key["id"])
    await test_session.refresh(stored_key)
    assert stored_key.revoked_at is not None

    rejected_response = await client.get(
        "/merchants/me",
        headers=authorization_header(new_key["api_key"]),
    )
    assert rejected_response.status_code == 401


@pytest.mark.asyncio
async def test_key_management_is_isolated_between_merchants(
    client: AsyncClient,
    test_session: AsyncSession,
    key_management_merchant,
):
    other_merchant = await create_merchant_account(
        test_session,
        name="Other Merchant",
        wallet_address="0x4444444444444444444444444444444444444444",
        webhook_url="https://other-merchant.test/webhooks/stablepay",
        api_key_name="Other key",
    )
    await test_session.commit()

    response = await client.delete(
        f"/merchants/me/api-keys/{other_merchant.api_key_id}",
        headers=authorization_header(key_management_merchant.api_key),
    )

    assert response.status_code == 404
    other_key = await test_session.get(MerchantApiKey, other_merchant.api_key_id)
    assert other_key.revoked_at is None

    list_response = await client.get(
        "/merchants/me/api-keys",
        headers=authorization_header(key_management_merchant.api_key),
    )
    listed_ids = {key["id"] for key in list_response.json()}
    assert other_merchant.api_key_id not in listed_ids


@pytest.mark.asyncio
async def test_revoking_unknown_key_returns_not_found(
    client: AsyncClient,
    key_management_merchant,
):
    response = await client.delete(
        "/merchants/me/api-keys/key_00000000000000000000000000000000",
        headers=authorization_header(key_management_merchant.api_key),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_merchant_cannot_revoke_its_last_active_key(
    client: AsyncClient,
    test_session: AsyncSession,
    key_management_merchant,
):
    response = await client.delete(
        f"/merchants/me/api-keys/{key_management_merchant.api_key_id}",
        headers=authorization_header(key_management_merchant.api_key),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Cannot revoke the merchant's last active API key"
    )
    stored_key = await test_session.get(
        MerchantApiKey,
        key_management_merchant.api_key_id,
    )
    assert stored_key.revoked_at is None

    still_authenticated = await client.get(
        "/merchants/me",
        headers=authorization_header(key_management_merchant.api_key),
    )
    assert still_authenticated.status_code == 200


@pytest.mark.asyncio
async def test_api_key_name_cannot_be_only_whitespace(
    client: AsyncClient,
    key_management_merchant,
):
    response = await client.post(
        "/merchants/me/api-keys",
        headers=authorization_header(key_management_merchant.api_key),
        json={"name": "   "},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_merchant_can_create_a_key_with_an_expiration(
    client: AsyncClient,
    key_management_merchant,
):
    expiration = datetime.now(timezone.utc) + timedelta(days=30)

    response = await client.post(
        "/merchants/me/api-keys",
        headers=authorization_header(key_management_merchant.api_key),
        json={
            "name": "Thirty day key",
            "expires_at": expiration.isoformat(),
        },
    )

    assert response.status_code == 201
    response_expiration = datetime.fromisoformat(response.json()["expires_at"])
    assert response_expiration == expiration


@pytest.mark.asyncio
async def test_merchant_cannot_create_an_already_expired_key(
    client: AsyncClient,
    key_management_merchant,
):
    expiration = datetime.now(timezone.utc) - timedelta(seconds=1)

    response = await client.post(
        "/merchants/me/api-keys",
        headers=authorization_header(key_management_merchant.api_key),
        json={
            "name": "Expired key",
            "expires_at": expiration.isoformat(),
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "API key expiration must be in the future"
