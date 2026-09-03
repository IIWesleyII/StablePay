import asyncio
import logging
from datetime import datetime
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.database import SessionLocal
from database.models import Payment
from database.models import VaultDeposit
from domain.ledger import VaultDepositStatus
from domain.payments import PaymentStatus
from services.payment_lifecycle import PaymentLifecycleError
from services.payment_lifecycle import mark_payment_expired


logger = logging.getLogger(__name__)

EXPIRATION_BATCH_SIZE = 100


async def expire_pending_payments(
    session: AsyncSession,
    current_time: datetime | None = None,
    batch_size: int = EXPIRATION_BATCH_SIZE,
) -> list[str]:
    """Expire one locked batch of pending payments whose deadline has passed."""

    if batch_size <= 0:
        raise ValueError("Expiration batch size must be positive")

    expiration_time = current_time or datetime.now(timezone.utc)
    if expiration_time.tzinfo is None or expiration_time.utcoffset() is None:
        raise PaymentLifecycleError("Expiration time must include a timezone")

    expiration_time = expiration_time.astimezone(timezone.utc)

    result = await session.execute(
        select(Payment)
        .where(
            Payment.status == PaymentStatus.PENDING,
            Payment.expires_at <= expiration_time,
        )
        .order_by(Payment.expires_at, Payment.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    payments = result.scalars().all()

    for payment in payments:
        mark_payment_expired(payment, expiration_time)

    await session.commit()

    return [payment.id for payment in payments]


async def run_payment_expiration_worker(stop_event: asyncio.Event) -> None:
    """Repeatedly expire overdue payments until application shutdown."""

    while not stop_event.is_set():
        async with SessionLocal() as session:
            try:
                expired_payment_ids = await expire_pending_payments(session)
                expired_deposit_ids = await expire_pending_vault_deposits(session)
            except asyncio.CancelledError:
                raise
            except Exception:
                await session.rollback()
                logger.exception("Payment expiration scan failed")
            else:
                if expired_payment_ids:
                    logger.info(
                        "Expired %s payment(s)",
                        len(expired_payment_ids),
                    )
                if expired_deposit_ids:
                    logger.info(
                        "Expired %s vault deposit(s)",
                        len(expired_deposit_ids),
                    )

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.payment_expiration_poll_seconds,
            )
        except TimeoutError:
            pass


async def expire_pending_vault_deposits(
    session: AsyncSession,
    current_time: datetime | None = None,
    batch_size: int = EXPIRATION_BATCH_SIZE,
) -> list[str]:
    """Expire one locked batch of overdue, unmatched vault deposits."""

    if batch_size <= 0:
        raise ValueError("Expiration batch size must be positive")
    expiration_time = current_time or datetime.now(timezone.utc)
    if expiration_time.tzinfo is None or expiration_time.utcoffset() is None:
        raise PaymentLifecycleError("Expiration time must include a timezone")
    expiration_time = expiration_time.astimezone(timezone.utc)

    result = await session.execute(
        select(VaultDeposit)
        .where(
            VaultDeposit.status == VaultDepositStatus.PENDING,
            VaultDeposit.expires_at <= expiration_time,
        )
        .order_by(VaultDeposit.expires_at, VaultDeposit.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    deposits = list(result.scalars())
    for deposit in deposits:
        deposit.status = VaultDepositStatus.EXPIRED
    await session.commit()
    return [deposit.id for deposit in deposits]
