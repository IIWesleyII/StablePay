"""Atomic, balanced bookkeeping for deposits and micropayments."""

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import LedgerAccount
from database.models import LedgerEntry
from database.models import LedgerTransaction
from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType


USDC_ATOMIC_SCALE = 1_000_000
SYSTEM_CUSTODY_OWNER_ID = "stablepay-custody"


class LedgerError(ValueError):
    """Raised when a transfer would violate a ledger invariant."""


class InsufficientBalanceError(LedgerError):
    """Raised when an account cannot fund a requested transfer."""


class IdempotencyConflictError(LedgerError):
    """Raised when an idempotency key is reused for different work."""


@dataclass(frozen=True)
class PostedLedgerTransfer:
    transaction: LedgerTransaction
    source_balance_atomic: int
    destination_balance_atomic: int
    replayed: bool


def usdc_to_atomic(amount: Decimal) -> int:
    """Convert a USDC decimal to its exact six-decimal integer value."""

    scaled = amount * USDC_ATOMIC_SCALE
    if amount <= 0:
        raise LedgerError("USDC amount must be positive")
    if scaled != scaled.to_integral_value():
        raise LedgerError("USDC amount cannot have more than 6 decimal places")
    return int(scaled)


def atomic_to_usdc(amount_atomic: int) -> Decimal:
    return Decimal(amount_atomic) / Decimal(USDC_ATOMIC_SCALE)


async def get_or_create_ledger_account(
    session: AsyncSession,
    owner_type: LedgerOwnerType,
    owner_id: str,
    current_time: datetime | None = None,
) -> LedgerAccount:
    result = await session.execute(
        select(LedgerAccount).where(
            LedgerAccount.owner_type == owner_type,
            LedgerAccount.owner_id == owner_id,
            LedgerAccount.currency == "USDC",
        )
    )
    account = result.scalar_one_or_none()
    if account is not None:
        return account

    account = LedgerAccount(
        id=f"lac_{uuid4().hex}",
        owner_type=owner_type,
        owner_id=owner_id,
        currency="USDC",
        balance_atomic=0,
        created_at=_as_utc(current_time or datetime.now(timezone.utc)),
    )
    session.add(account)
    await session.flush()
    return account


async def post_ledger_transfer(
    session: AsyncSession,
    *,
    source_account: LedgerAccount,
    destination_account: LedgerAccount,
    amount_atomic: int,
    transaction_type: LedgerTransactionType,
    idempotency_key: str,
    reference_id: str,
    current_time: datetime | None = None,
) -> PostedLedgerTransfer:
    """Stage one two-sided transfer without committing the caller's session."""

    if amount_atomic <= 0:
        raise LedgerError("Ledger transfer amount must be positive")
    if source_account.id == destination_account.id:
        raise LedgerError("Ledger source and destination must be different")
    if not idempotency_key or len(idempotency_key) > 255:
        raise LedgerError("Ledger idempotency key must contain 1 to 255 characters")

    existing_result = await session.execute(
        select(LedgerTransaction).where(
            LedgerTransaction.idempotency_key == idempotency_key
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        _require_same_transfer(
            existing,
            source_account,
            destination_account,
            amount_atomic,
            transaction_type,
            reference_id,
        )
        await session.refresh(source_account)
        await session.refresh(destination_account)
        return PostedLedgerTransfer(
            transaction=existing,
            source_balance_atomic=source_account.balance_atomic,
            destination_balance_atomic=destination_account.balance_atomic,
            replayed=True,
        )

    locked_result = await session.execute(
        select(LedgerAccount)
        .where(
            LedgerAccount.id.in_([source_account.id, destination_account.id])
        )
        .order_by(LedgerAccount.id)
        .with_for_update()
    )
    locked = {account.id: account for account in locked_result.scalars()}
    if len(locked) != 2:
        raise LedgerError("Ledger account could not be loaded")

    locked_source = locked[source_account.id]
    locked_destination = locked[destination_account.id]
    if (
        locked_source.owner_type != LedgerOwnerType.SYSTEM
        and locked_source.balance_atomic < amount_atomic
    ):
        raise InsufficientBalanceError("Vault has insufficient USDC balance")

    posted_at = _as_utc(current_time or datetime.now(timezone.utc))
    transaction = LedgerTransaction(
        id=f"ltx_{uuid4().hex}",
        transaction_type=transaction_type,
        idempotency_key=idempotency_key,
        source_account_id=locked_source.id,
        destination_account_id=locked_destination.id,
        amount_atomic=amount_atomic,
        reference_id=reference_id,
        created_at=posted_at,
    )
    debit = LedgerEntry(
        id=f"len_{uuid4().hex}",
        transaction_id=transaction.id,
        account_id=locked_source.id,
        amount_atomic=-amount_atomic,
        created_at=posted_at,
    )
    credit = LedgerEntry(
        id=f"len_{uuid4().hex}",
        transaction_id=transaction.id,
        account_id=locked_destination.id,
        amount_atomic=amount_atomic,
        created_at=posted_at,
    )
    locked_source.balance_atomic -= amount_atomic
    locked_destination.balance_atomic += amount_atomic
    # Flush the parent first because entry IDs reference it directly without an
    # ORM relationship that SQLAlchemy could otherwise use to infer ordering.
    session.add(transaction)
    await session.flush()
    session.add_all([debit, credit])
    await session.flush()

    return PostedLedgerTransfer(
        transaction=transaction,
        source_balance_atomic=locked_source.balance_atomic,
        destination_balance_atomic=locked_destination.balance_atomic,
        replayed=False,
    )


async def account_entries_sum(
    session: AsyncSession,
    account_id: str,
) -> int:
    """Recompute an account balance from its immutable history."""

    result = await session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount_atomic), 0)).where(
            LedgerEntry.account_id == account_id
        )
    )
    return int(result.scalar_one())


def _require_same_transfer(
    existing: LedgerTransaction,
    source: LedgerAccount,
    destination: LedgerAccount,
    amount_atomic: int,
    transaction_type: LedgerTransactionType,
    reference_id: str,
) -> None:
    if (
        existing.source_account_id != source.id
        or existing.destination_account_id != destination.id
        or existing.amount_atomic != amount_atomic
        or existing.transaction_type != transaction_type
        or existing.reference_id != reference_id
    ):
        raise IdempotencyConflictError(
            "Idempotency key was already used for a different ledger transfer"
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LedgerError("Ledger timestamp must include a timezone")
    return value.astimezone(timezone.utc)
