from datetime import datetime
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from blockchain.base import TransactionFailedError
from blockchain.base import TransactionNotMinedError
from blockchain.base import UsdcTransfer
from config import settings
from database.models import Merchant
from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType
from domain.settlements import SettlementStatus
from services.ledger import SYSTEM_CUSTODY_OWNER_ID
from services.ledger import get_or_create_ledger_account
from services.ledger import post_ledger_transfer
from services.settlement_monitor import refresh_submitted_settlements
from services.settlements import create_settlement
from services.settlements import prepare_settlement_broadcast


NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
VAULT_ADDRESS = "0x1111111111111111111111111111111111111111"
MERCHANT_ADDRESS = "0x2222222222222222222222222222222222222222"
TRANSACTION_HASH = "0x" + "ab" * 32


class FakeSettlementClient:
    def __init__(self, result):
        self.result = result

    async def get_usdc_transfers(self, transaction_hash: str):
        assert transaction_hash == TRANSACTION_HASH
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def make_broadcasting_settlement(session: AsyncSession):
    merchant = Merchant(
        id="mch_monitor_test",
        name="Monitor Test",
        wallet_address=MERCHANT_ADDRESS,
        webhook_url="https://merchant.test/webhook",
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(merchant)
    await session.flush()
    custody = await get_or_create_ledger_account(
        session, LedgerOwnerType.SYSTEM, SYSTEM_CUSTODY_OWNER_ID, NOW
    )
    merchant_account = await get_or_create_ledger_account(
        session, LedgerOwnerType.MERCHANT, merchant.id, NOW
    )
    await post_ledger_transfer(
        session,
        source_account=custody,
        destination_account=merchant_account,
        amount_atomic=1_000,
        transaction_type=LedgerTransactionType.MICROPAYMENT,
        idempotency_key="monitor-funding",
        reference_id="funding",
        current_time=NOW,
    )
    created = await create_settlement(
        session,
        merchant,
        amount_atomic=None,
        minimum_amount_atomic=1,
        idempotency_key="monitor-settlement",
        current_time=NOW,
    )
    await prepare_settlement_broadcast(
        session,
        created.settlement.id,
        transaction_hash=TRANSACTION_HASH,
        transaction_nonce=4,
        signed_transaction="0x" + "12" * 100,
        current_time=NOW,
    )
    await session.commit()
    return created.settlement, merchant_account


def matching_transfer(confirmations: int = 3) -> UsdcTransfer:
    return UsdcTransfer(
        transaction_hash=TRANSACTION_HASH,
        log_index=0,
        sender=VAULT_ADDRESS,
        recipient=MERCHANT_ADDRESS,
        raw_amount=1_000,
        amount=Decimal("0.001"),
        block_number=100,
        confirmations=confirmations,
    )


@pytest.mark.asyncio
async def test_monitor_confirms_exact_settlement_transfer(
    test_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(settings, "stablepay_vault_address", VAULT_ADDRESS)
    settlement, _ = await make_broadcasting_settlement(test_session)

    result = await refresh_submitted_settlements(
        test_session,
        FakeSettlementClient([matching_transfer()]),
        NOW,
    )

    assert result.confirmed == 1
    assert settlement.status == SettlementStatus.CONFIRMED
    assert settlement.signed_transaction is None


@pytest.mark.asyncio
async def test_monitor_waits_for_receipt_without_changing_reserve(
    test_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(settings, "stablepay_vault_address", VAULT_ADDRESS)
    settlement, merchant_account = await make_broadcasting_settlement(test_session)

    result = await refresh_submitted_settlements(
        test_session,
        FakeSettlementClient(TransactionNotMinedError("not mined")),
        NOW,
    )

    assert result.awaiting_receipt == 1
    assert settlement.status == SettlementStatus.BROADCASTING
    assert merchant_account.balance_atomic == 0


@pytest.mark.asyncio
async def test_monitor_releases_balance_after_reverted_transaction(
    test_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(settings, "stablepay_vault_address", VAULT_ADDRESS)
    settlement, merchant_account = await make_broadcasting_settlement(test_session)

    result = await refresh_submitted_settlements(
        test_session,
        FakeSettlementClient(TransactionFailedError("reverted")),
        NOW,
    )

    assert result.failed == 1
    assert settlement.status == SettlementStatus.FAILED
    assert merchant_account.balance_atomic == 1_000


@pytest.mark.asyncio
async def test_monitor_freezes_mismatched_success_for_review(
    test_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(settings, "stablepay_vault_address", VAULT_ADDRESS)
    settlement, merchant_account = await make_broadcasting_settlement(test_session)
    wrong_amount = matching_transfer()
    wrong_amount = UsdcTransfer(
        **{**wrong_amount.__dict__, "raw_amount": 999, "amount": Decimal("0.000999")}
    )

    result = await refresh_submitted_settlements(
        test_session,
        FakeSettlementClient([wrong_amount]),
        NOW,
    )

    assert result.review_required == 1
    assert settlement.status == SettlementStatus.REVIEW_REQUIRED
    assert merchant_account.balance_atomic == 0
