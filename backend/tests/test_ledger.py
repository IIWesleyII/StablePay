from datetime import datetime
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import LedgerEntry
from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType
from services.ledger import IdempotencyConflictError
from services.ledger import InsufficientBalanceError
from services.ledger import account_entries_sum
from services.ledger import get_or_create_ledger_account
from services.ledger import post_ledger_transfer
from services.ledger import usdc_to_atomic


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_transfer_creates_balanced_entries_and_cached_balances(
    test_session: AsyncSession,
):
    system = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.SYSTEM, "custody", NOW
    )
    vault = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.VAULT, "vlt_test", NOW
    )

    posted = await post_ledger_transfer(
        test_session,
        source_account=system,
        destination_account=vault,
        amount_atomic=usdc_to_atomic(Decimal("5.000000")),
        transaction_type=LedgerTransactionType.DEPOSIT,
        idempotency_key="deposit:test:0",
        reference_id="dep_test",
        current_time=NOW,
    )
    await test_session.commit()

    entries_result = await test_session.execute(
        select(LedgerEntry).where(
            LedgerEntry.transaction_id == posted.transaction.id
        )
    )
    entries = list(entries_result.scalars())
    assert len(entries) == 2
    assert sum(entry.amount_atomic for entry in entries) == 0
    assert system.balance_atomic == -5_000_000
    assert vault.balance_atomic == 5_000_000
    assert await account_entries_sum(test_session, system.id) == system.balance_atomic
    assert await account_entries_sum(test_session, vault.id) == vault.balance_atomic


@pytest.mark.asyncio
async def test_idempotent_retry_does_not_move_money_twice(
    test_session: AsyncSession,
):
    system = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.SYSTEM, "custody", NOW
    )
    vault = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.VAULT, "vlt_test", NOW
    )
    arguments = {
        "source_account": system,
        "destination_account": vault,
        "amount_atomic": 1_000,
        "transaction_type": LedgerTransactionType.DEPOSIT,
        "idempotency_key": "same-request",
        "reference_id": "dep_test",
        "current_time": NOW,
    }

    first = await post_ledger_transfer(test_session, **arguments)
    second = await post_ledger_transfer(test_session, **arguments)
    await test_session.commit()

    entry_count = await test_session.scalar(
        select(func.count()).select_from(LedgerEntry)
    )
    assert first.replayed is False
    assert second.replayed is True
    assert first.transaction.id == second.transaction.id
    assert entry_count == 2
    assert vault.balance_atomic == 1_000


@pytest.mark.asyncio
async def test_reused_idempotency_key_must_describe_same_transfer(
    test_session: AsyncSession,
):
    system = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.SYSTEM, "custody", NOW
    )
    vault = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.VAULT, "vlt_test", NOW
    )
    await post_ledger_transfer(
        test_session,
        source_account=system,
        destination_account=vault,
        amount_atomic=1_000,
        transaction_type=LedgerTransactionType.DEPOSIT,
        idempotency_key="same-request",
        reference_id="dep_test",
        current_time=NOW,
    )

    with pytest.raises(IdempotencyConflictError):
        await post_ledger_transfer(
            test_session,
            source_account=system,
            destination_account=vault,
            amount_atomic=2_000,
            transaction_type=LedgerTransactionType.DEPOSIT,
            idempotency_key="same-request",
            reference_id="dep_test",
            current_time=NOW,
        )


@pytest.mark.asyncio
async def test_non_system_account_cannot_overdraw(test_session: AsyncSession):
    vault = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.VAULT, "vlt_test", NOW
    )
    merchant = await get_or_create_ledger_account(
        test_session, LedgerOwnerType.MERCHANT, "mch_test", NOW
    )

    with pytest.raises(InsufficientBalanceError):
        await post_ledger_transfer(
            test_session,
            source_account=vault,
            destination_account=merchant,
            amount_atomic=1,
            transaction_type=LedgerTransactionType.MICROPAYMENT,
            idempotency_key="spend-too-much",
            reference_id="api-call-1",
            current_time=NOW,
        )

    assert vault.balance_atomic == 0
    assert merchant.balance_atomic == 0


def test_usdc_atomic_conversion_preserves_micropayment_precision():
    assert usdc_to_atomic(Decimal("0.001")) == 1_000
