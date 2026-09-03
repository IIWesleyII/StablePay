from datetime import datetime
from datetime import timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Vault
from services.vaults import VaultError
from services.vaults import create_vault_challenge
from services.vaults import create_vault_from_signature
from services.vaults import verify_vault_access_token


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_wallet_signature_opens_vault_and_secret_is_only_hashed(
    test_session: AsyncSession,
):
    wallet = Account.create()
    challenge = await create_vault_challenge(
        test_session,
        wallet.address,
        NOW,
    )
    signature = Account.sign_message(
        encode_defunct(text=challenge.message),
        wallet.key,
    ).signature.hex()

    created = await create_vault_from_signature(
        test_session,
        challenge.id,
        signature,
        NOW,
    )
    await test_session.commit()

    stored = await test_session.get(Vault, created.vault.id)
    assert stored is not None
    assert stored.access_token_hash != created.access_token
    assert verify_vault_access_token(
        created.access_token,
        stored.access_token_hash,
    )
    assert challenge.used_at == NOW


@pytest.mark.asyncio
async def test_challenge_cannot_be_replayed(test_session: AsyncSession):
    wallet = Account.create()
    challenge = await create_vault_challenge(test_session, wallet.address, NOW)
    signature = Account.sign_message(
        encode_defunct(text=challenge.message), wallet.key
    ).signature.hex()
    await create_vault_from_signature(
        test_session, challenge.id, signature, NOW
    )

    with pytest.raises(VaultError, match="already been used"):
        await create_vault_from_signature(
            test_session, challenge.id, signature, NOW
        )


@pytest.mark.asyncio
async def test_signature_must_match_challenge_wallet(
    test_session: AsyncSession,
):
    requested_wallet = Account.create()
    other_wallet = Account.create()
    challenge = await create_vault_challenge(
        test_session, requested_wallet.address, NOW
    )
    wrong_signature = Account.sign_message(
        encode_defunct(text=challenge.message), other_wallet.key
    ).signature.hex()

    with pytest.raises(VaultError, match="does not match"):
        await create_vault_from_signature(
            test_session, challenge.id, wrong_signature, NOW
        )
