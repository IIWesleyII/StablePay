from datetime import datetime
from datetime import timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Merchant
from database.models import MerchantApiKey
from services.api_keys import generate_merchant_api_key


def make_merchant() -> Merchant:
    current_time = datetime.now(timezone.utc)
    return Merchant(
        id="mch_model_test",
        name="Test Merchant",
        wallet_address="0x2222222222222222222222222222222222222222",
        webhook_url="https://merchant.test/webhooks/stablepay",
        created_at=current_time,
        updated_at=current_time,
    )


@pytest.mark.asyncio
async def test_merchant_and_hashed_api_key_are_persisted(
    test_session: AsyncSession,
):
    merchant = make_merchant()
    generated = generate_merchant_api_key(merchant.id, "Development")
    test_session.add_all([merchant, generated.record])
    await test_session.commit()

    key_result = await test_session.execute(
        select(MerchantApiKey).where(MerchantApiKey.id == generated.record.id)
    )
    stored_key = key_result.scalar_one()

    assert merchant.is_active is True
    assert stored_key.merchant_id == merchant.id
    assert stored_key.key_prefix == generated.plaintext[:32]
    assert stored_key.secret_hash != generated.plaintext
    assert stored_key.last_used_at is None
    assert stored_key.expires_at is None
    assert stored_key.revoked_at is None


@pytest.mark.asyncio
async def test_api_key_hash_must_be_unique(test_session: AsyncSession):
    merchant = make_merchant()
    generated = generate_merchant_api_key(merchant.id, "First")
    duplicate = MerchantApiKey(
        id="key_duplicate_hash",
        merchant_id=merchant.id,
        name="Duplicate",
        key_prefix=generated.record.key_prefix,
        secret_hash=generated.record.secret_hash,
        created_at=datetime.now(timezone.utc),
    )
    test_session.add_all([merchant, generated.record, duplicate])

    with pytest.raises(IntegrityError):
        await test_session.commit()

    await test_session.rollback()
