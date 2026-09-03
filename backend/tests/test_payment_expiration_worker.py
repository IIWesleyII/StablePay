from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment
from database.models import Vault
from database.models import VaultDeposit
from domain.ledger import VaultDepositStatus
from domain.payments import PaymentStatus
from services.payment_lifecycle import PaymentLifecycleError
from workers.payment_expiration import expire_pending_payments
from workers.payment_expiration import expire_pending_vault_deposits


CURRENT_TIME = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def make_payment(
    payment_id: str,
    expires_at: datetime,
    status: PaymentStatus = PaymentStatus.PENDING,
) -> Payment:
    return Payment(
        id=payment_id,
        amount=Decimal("1.00"),
        currency="USDC",
        chain="base-sepolia",
        recipient_address="0x1111111111111111111111111111111111111111",
        status=status,
        created_at=CURRENT_TIME - timedelta(minutes=15),
        expires_at=expires_at,
    )


@pytest.mark.asyncio
async def test_worker_expires_only_overdue_pending_payments(
    test_session: AsyncSession,
):
    overdue = make_payment(
        "pay_overdue",
        CURRENT_TIME - timedelta(seconds=1),
    )
    not_due = make_payment(
        "pay_not_due",
        CURRENT_TIME + timedelta(seconds=1),
    )
    confirming = make_payment(
        "pay_confirming",
        CURRENT_TIME - timedelta(minutes=1),
        PaymentStatus.CONFIRMING,
    )
    test_session.add_all([overdue, not_due, confirming])
    await test_session.commit()

    expired_ids = await expire_pending_payments(test_session, CURRENT_TIME)

    assert expired_ids == ["pay_overdue"]
    assert overdue.status is PaymentStatus.EXPIRED
    assert not_due.status is PaymentStatus.PENDING
    assert confirming.status is PaymentStatus.CONFIRMING


@pytest.mark.asyncio
async def test_worker_treats_exact_deadline_as_expired(
    test_session: AsyncSession,
):
    payment = make_payment("pay_at_deadline", CURRENT_TIME)
    test_session.add(payment)
    await test_session.commit()

    expired_ids = await expire_pending_payments(test_session, CURRENT_TIME)

    assert expired_ids == ["pay_at_deadline"]
    assert payment.status is PaymentStatus.EXPIRED


@pytest.mark.asyncio
async def test_worker_respects_batch_size(test_session: AsyncSession):
    oldest = make_payment(
        "pay_oldest",
        CURRENT_TIME - timedelta(minutes=2),
    )
    newer = make_payment(
        "pay_newer",
        CURRENT_TIME - timedelta(minutes=1),
    )
    test_session.add_all([newer, oldest])
    await test_session.commit()

    first_batch = await expire_pending_payments(
        test_session,
        CURRENT_TIME,
        batch_size=1,
    )
    second_batch = await expire_pending_payments(
        test_session,
        CURRENT_TIME,
        batch_size=1,
    )

    assert first_batch == ["pay_oldest"]
    assert second_batch == ["pay_newer"]


@pytest.mark.asyncio
async def test_worker_rejects_timestamp_without_timezone(
    test_session: AsyncSession,
):
    timestamp_without_timezone = datetime(2026, 8, 28, 12, 0)

    with pytest.raises(PaymentLifecycleError, match="must include a timezone"):
        await expire_pending_payments(
            test_session,
            timestamp_without_timezone,
        )


@pytest.mark.asyncio
async def test_worker_rejects_non_positive_batch_size(
    test_session: AsyncSession,
):
    with pytest.raises(ValueError, match="batch size must be positive"):
        await expire_pending_payments(test_session, CURRENT_TIME, batch_size=0)


@pytest.mark.asyncio
async def test_worker_expires_overdue_vault_deposit(
    test_session: AsyncSession,
):
    vault = Vault(
        id="vlt_expiration_test",
        wallet_address="0x4444444444444444444444444444444444444444",
        access_token_prefix="prefix",
        access_token_hash="a" * 64,
        is_active=True,
        created_at=CURRENT_TIME - timedelta(minutes=20),
    )
    deposit = VaultDeposit(
        id="dep_overdue",
        vault_id=vault.id,
        amount_atomic=1_000_000,
        status=VaultDepositStatus.PENDING,
        created_at=CURRENT_TIME - timedelta(minutes=20),
        expires_at=CURRENT_TIME - timedelta(minutes=5),
    )
    test_session.add_all([vault, deposit])
    await test_session.commit()

    expired_ids = await expire_pending_vault_deposits(
        test_session,
        CURRENT_TIME,
    )

    assert expired_ids == [deposit.id]
    assert deposit.status == VaultDepositStatus.EXPIRED
