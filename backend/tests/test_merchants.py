from datetime import datetime
from datetime import timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Merchant
from database.models import MerchantApiKey
from services.api_keys import verify_api_key
from services.merchants import MerchantAccountError
from services.merchants import create_merchant_account


WALLET_ADDRESS = "0x2222222222222222222222222222222222222222"
WEBHOOK_URL = "https://merchant.test/webhooks/stablepay"


@pytest.mark.asyncio
async def test_merchant_account_and_initial_key_are_created_together(
    test_session: AsyncSession,
):
    created = await create_merchant_account(
        test_session,
        name="  Example Merchant  ",
        wallet_address=WALLET_ADDRESS,
        webhook_url=WEBHOOK_URL,
        api_key_name="Development",
    )
    await test_session.commit()

    merchant_result = await test_session.execute(
        select(Merchant).where(Merchant.id == created.merchant.id)
    )
    key_result = await test_session.execute(
        select(MerchantApiKey).where(MerchantApiKey.id == created.api_key_id)
    )
    merchant = merchant_result.scalar_one()
    stored_key = key_result.scalar_one()

    assert merchant.name == "Example Merchant"
    assert merchant.wallet_address == WALLET_ADDRESS
    assert merchant.webhook_url == WEBHOOK_URL
    assert merchant.is_active is True
    assert verify_api_key(created.api_key, stored_key.secret_hash)
    assert created.api_key not in repr(created)


@pytest.mark.asyncio
async def test_duplicate_merchant_wallet_is_rejected(
    test_session: AsyncSession,
):
    await create_merchant_account(
        test_session,
        name="First Merchant",
        wallet_address=WALLET_ADDRESS,
        webhook_url=WEBHOOK_URL,
    )
    await test_session.commit()

    with pytest.raises(MerchantAccountError, match="already exists"):
        await create_merchant_account(
            test_session,
            name="Duplicate Merchant",
            wallet_address=WALLET_ADDRESS.lower(),
            webhook_url=WEBHOOK_URL,
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "error_message"),
    [
        ("name", "   ", "name must not be empty"),
        ("wallet_address", "not-an-address", "valid Ethereum address"),
        ("webhook_url", "not-a-url", "valid HTTP"),
        (
            "webhook_url",
            "https://user:password@merchant.test/webhook",
            "must not contain credentials",
        ),
        (
            "webhook_url",
            "https://merchant.test/webhook#fragment",
            "must not contain a fragment",
        ),
    ],
)
@pytest.mark.asyncio
async def test_invalid_merchant_data_is_rejected(
    test_session: AsyncSession,
    field_name: str,
    invalid_value: str,
    error_message: str,
):
    values = {
        "name": "Example Merchant",
        "wallet_address": WALLET_ADDRESS,
        "webhook_url": WEBHOOK_URL,
    }
    values[field_name] = invalid_value

    with pytest.raises(MerchantAccountError, match=error_message):
        await create_merchant_account(test_session, **values)


@pytest.mark.asyncio
async def test_merchant_creation_time_requires_timezone(
    test_session: AsyncSession,
):
    with pytest.raises(MerchantAccountError, match="must include a timezone"):
        await create_merchant_account(
            test_session,
            name="Example Merchant",
            wallet_address=WALLET_ADDRESS,
            webhook_url=WEBHOOK_URL,
            created_at=datetime(2026, 9, 1, 12, 0),
        )
