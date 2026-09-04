from datetime import datetime
from datetime import timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import LedgerAccount
from database.models import Merchant
from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType
from domain.settlements import SettlementStatus
from services.ledger import SYSTEM_CUSTODY_OWNER_ID
from services.ledger import get_or_create_ledger_account
from services.ledger import post_ledger_transfer
from services.settlements import SettlementError
from services.settlements import SettlementIdempotencyError
from services.settlements import cancel_settlement
from services.settlements import confirm_settlement
from services.settlements import create_settlement
from services.settlements import fail_settlement
from services.settlements import mark_settlement_submitted
from services.settlements import prepare_settlement_broadcast
from services.settlements import require_settlement_review


NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
TRANSACTION_HASH = "0x" + "ab" * 32
SIGNED_TRANSACTION = "0x" + "12" * 120


async def make_funded_merchant(
    session: AsyncSession,
    balance_atomic: int = 10_000,
) -> tuple[Merchant, LedgerAccount]:
    merchant = Merchant(
        id="mch_settlement_test",
        name="Settlement Test Merchant",
        wallet_address="0x2222222222222222222222222222222222222222",
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
        amount_atomic=balance_atomic,
        transaction_type=LedgerTransactionType.MICROPAYMENT,
        idempotency_key="merchant-test-funding",
        reference_id="test-funding",
        current_time=NOW,
    )
    await session.commit()
    return merchant, merchant_account


@pytest.mark.asyncio
async def test_create_settlement_reserves_all_available_balance(
    test_session: AsyncSession,
):
    merchant, merchant_account = await make_funded_merchant(test_session)

    created = await create_settlement(
        test_session,
        merchant,
        amount_atomic=None,
        minimum_amount_atomic=1,
        idempotency_key="settle-all",
        current_time=NOW,
    )
    await test_session.commit()
    reserve = await test_session.scalar(
        select(LedgerAccount).where(
            LedgerAccount.owner_type == LedgerOwnerType.SYSTEM,
            LedgerAccount.owner_id == created.settlement.id,
        )
    )

    assert created.replayed is False
    assert created.settlement.amount_atomic == 10_000
    assert created.settlement.status == SettlementStatus.PENDING
    assert merchant_account.balance_atomic == 0
    assert reserve is not None
    assert reserve.balance_atomic == 10_000


@pytest.mark.asyncio
async def test_settlement_request_is_idempotent(test_session: AsyncSession):
    merchant, _ = await make_funded_merchant(test_session)
    arguments = {
        "amount_atomic": 5_000,
        "minimum_amount_atomic": 1,
        "idempotency_key": "same-settlement",
        "current_time": NOW,
    }
    first = await create_settlement(test_session, merchant, **arguments)
    second = await create_settlement(test_session, merchant, **arguments)

    assert first.replayed is False
    assert second.replayed is True
    assert first.settlement.id == second.settlement.id

    with pytest.raises(SettlementIdempotencyError):
        await create_settlement(
            test_session,
            merchant,
            amount_atomic=4_000,
            minimum_amount_atomic=1,
            idempotency_key="same-settlement",
            current_time=NOW,
        )


@pytest.mark.asyncio
async def test_cancel_pending_settlement_restores_balance(
    test_session: AsyncSession,
):
    merchant, merchant_account = await make_funded_merchant(test_session)
    created = await create_settlement(
        test_session,
        merchant,
        amount_atomic=4_000,
        minimum_amount_atomic=1,
        idempotency_key="cancel-me",
        current_time=NOW,
    )

    cancelled = await cancel_settlement(
        test_session, created.settlement.id, merchant.id, NOW
    )
    await test_session.commit()

    assert cancelled.status == SettlementStatus.CANCELLED
    assert merchant_account.balance_atomic == 10_000


@pytest.mark.asyncio
async def test_confirmed_settlement_clears_reserve_to_custody(
    test_session: AsyncSession,
):
    merchant, _ = await make_funded_merchant(test_session)
    created = await create_settlement(
        test_session,
        merchant,
        amount_atomic=4_000,
        minimum_amount_atomic=1,
        idempotency_key="confirm-me",
        current_time=NOW,
    )
    await prepare_settlement_broadcast(
        test_session,
        created.settlement.id,
        transaction_hash=TRANSACTION_HASH,
        transaction_nonce=7,
        signed_transaction=SIGNED_TRANSACTION,
        current_time=NOW,
    )
    await mark_settlement_submitted(
        test_session, created.settlement.id, TRANSACTION_HASH, NOW
    )

    confirmed = await confirm_settlement(
        test_session, created.settlement.id, NOW
    )
    await test_session.commit()
    reserve = await test_session.scalar(
        select(LedgerAccount).where(
            LedgerAccount.owner_type == LedgerOwnerType.SYSTEM,
            LedgerAccount.owner_id == confirmed.id,
        )
    )

    assert confirmed.status == SettlementStatus.CONFIRMED
    assert confirmed.completion_ledger_transaction_id is not None
    assert confirmed.signed_transaction is None
    assert reserve is not None
    assert reserve.balance_atomic == 0


@pytest.mark.asyncio
async def test_reverted_settlement_releases_merchant_balance(
    test_session: AsyncSession,
):
    merchant, merchant_account = await make_funded_merchant(test_session)
    created = await create_settlement(
        test_session,
        merchant,
        amount_atomic=4_000,
        minimum_amount_atomic=1,
        idempotency_key="fail-me",
        current_time=NOW,
    )
    await prepare_settlement_broadcast(
        test_session,
        created.settlement.id,
        transaction_hash=TRANSACTION_HASH,
        transaction_nonce=7,
        signed_transaction=SIGNED_TRANSACTION,
        current_time=NOW,
    )

    failed = await fail_settlement(
        test_session, created.settlement.id, "Transaction reverted", NOW
    )
    await test_session.commit()

    assert failed.status == SettlementStatus.FAILED
    assert merchant_account.balance_atomic == 10_000


@pytest.mark.asyncio
async def test_uncertain_settlement_freezes_reserve_for_review(
    test_session: AsyncSession,
):
    merchant, merchant_account = await make_funded_merchant(test_session)
    created = await create_settlement(
        test_session,
        merchant,
        amount_atomic=4_000,
        minimum_amount_atomic=1,
        idempotency_key="review-me",
        current_time=NOW,
    )
    await prepare_settlement_broadcast(
        test_session,
        created.settlement.id,
        transaction_hash=TRANSACTION_HASH,
        transaction_nonce=7,
        signed_transaction=SIGNED_TRANSACTION,
        current_time=NOW,
    )

    reviewed = await require_settlement_review(
        test_session,
        created.settlement.id,
        "Successful receipt did not contain expected transfer",
    )

    assert reviewed.status == SettlementStatus.REVIEW_REQUIRED
    assert merchant_account.balance_atomic == 6_000
    with pytest.raises(SettlementError):
        await cancel_settlement(
            test_session, created.settlement.id, merchant.id, NOW
        )
