from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blockchain.base import UsdcTransfer
from config import settings
from database.models import LedgerAccount
from database.models import LedgerEntry
from database.models import Vault
from database.models import VaultDeposit
from domain.ledger import LedgerOwnerType
from domain.ledger import VaultDepositStatus
from services.vault_monitor import reconcile_vault_transfer


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
VAULT_ADDRESS = "0x3333333333333333333333333333333333333333"
SENDER = "0x4444444444444444444444444444444444444444"


def make_transfer() -> UsdcTransfer:
    return UsdcTransfer(
        transaction_hash="0x" + "a" * 64,
        log_index=2,
        sender=SENDER,
        recipient=VAULT_ADDRESS,
        raw_amount=5_000_000,
        amount=5,
        block_number=100,
        confirmations=3,
        block_hash="0x" + "b" * 64,
        block_timestamp=NOW + timedelta(minutes=1),
    )


@pytest.mark.asyncio
async def test_confirmed_transfer_credits_vault_exactly_once(
    test_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(settings, "stablepay_vault_address", VAULT_ADDRESS)
    vault = Vault(
        id="vlt_test",
        wallet_address=SENDER,
        access_token_prefix="prefix",
        access_token_hash="a" * 64,
        is_active=True,
        created_at=NOW,
    )
    deposit = VaultDeposit(
        id="dep_test",
        vault_id=vault.id,
        amount_atomic=5_000_000,
        status=VaultDepositStatus.PENDING,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    test_session.add_all([vault, deposit])
    await test_session.commit()

    first = await reconcile_vault_transfer(test_session, make_transfer(), NOW)
    second = await reconcile_vault_transfer(test_session, make_transfer(), NOW)
    await test_session.commit()

    vault_account = await test_session.scalar(
        select(LedgerAccount).where(
            LedgerAccount.owner_type == LedgerOwnerType.VAULT,
            LedgerAccount.owner_id == vault.id,
        )
    )
    entries = list((await test_session.scalars(select(LedgerEntry))).all())
    assert first == "matched"
    assert second == "already_matched"
    assert deposit.status == VaultDepositStatus.CONFIRMED
    assert deposit.transaction_hash == make_transfer().transaction_hash
    assert vault_account is not None
    assert vault_account.balance_atomic == 5_000_000
    assert len(entries) == 2
    assert sum(entry.amount_atomic for entry in entries) == 0


@pytest.mark.asyncio
async def test_transfer_must_match_sender_amount_and_time(
    test_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(settings, "stablepay_vault_address", VAULT_ADDRESS)
    vault = Vault(
        id="vlt_test",
        wallet_address=SENDER,
        access_token_prefix="prefix",
        access_token_hash="a" * 64,
        is_active=True,
        created_at=NOW,
    )
    deposit = VaultDeposit(
        id="dep_test",
        vault_id=vault.id,
        amount_atomic=1_000_000,
        status=VaultDepositStatus.PENDING,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    test_session.add_all([vault, deposit])
    await test_session.commit()

    result = await reconcile_vault_transfer(test_session, make_transfer(), NOW)

    assert result == "ignored"
    assert deposit.status == VaultDepositStatus.PENDING
