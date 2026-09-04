"""Merchant settlement reservation and lifecycle accounting."""

import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import LedgerAccount
from database.models import Merchant
from database.models import Settlement
from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType
from domain.settlements import SettlementStatus
from services.ledger import SYSTEM_CUSTODY_OWNER_ID
from services.ledger import get_or_create_ledger_account
from services.ledger import post_ledger_transfer


TRANSACTION_HASH_PATTERN = re.compile(r"0x[0-9a-fA-F]{64}")


class SettlementError(ValueError):
    """Raised when a settlement would violate its lifecycle or accounting."""


class SettlementBalanceError(SettlementError):
    """Raised when a merchant cannot fund the requested settlement."""


class SettlementIdempotencyError(SettlementError):
    """Raised when a settlement idempotency key is reused differently."""


@dataclass(frozen=True)
class CreatedSettlement:
    settlement: Settlement
    replayed: bool


async def create_settlement(
    session: AsyncSession,
    merchant: Merchant,
    *,
    amount_atomic: int | None,
    minimum_amount_atomic: int,
    idempotency_key: str,
    current_time: datetime | None = None,
) -> CreatedSettlement:
    """Reserve merchant balance for one future aggregate blockchain payout."""

    normalized_key = idempotency_key.strip()
    if not normalized_key or len(normalized_key) > 128:
        raise SettlementError(
            "Settlement idempotency key must contain 1 to 128 characters"
        )
    if amount_atomic is not None and amount_atomic <= 0:
        raise SettlementError("Settlement amount must be positive")
    if minimum_amount_atomic <= 0:
        raise SettlementError("Settlement minimum amount must be positive")

    existing_result = await session.execute(
        select(Settlement).where(
            Settlement.merchant_id == merchant.id,
            Settlement.idempotency_key == normalized_key,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        if amount_atomic is not None and existing.amount_atomic != amount_atomic:
            raise SettlementIdempotencyError(
                "Idempotency key was already used for a different settlement amount"
            )
        return CreatedSettlement(settlement=existing, replayed=True)

    merchant_account = await get_or_create_ledger_account(
        session,
        LedgerOwnerType.MERCHANT,
        merchant.id,
    )
    locked_result = await session.execute(
        select(LedgerAccount)
        .where(LedgerAccount.id == merchant_account.id)
        .with_for_update()
    )
    merchant_account = locked_result.scalar_one()
    settlement_amount = (
        merchant_account.balance_atomic
        if amount_atomic is None
        else amount_atomic
    )
    if settlement_amount < minimum_amount_atomic:
        raise SettlementBalanceError(
            "Merchant balance is below the minimum settlement amount"
        )
    if merchant_account.balance_atomic < settlement_amount:
        raise SettlementBalanceError(
            "Merchant has insufficient available balance for settlement"
        )

    created_at = _as_utc(current_time or datetime.now(timezone.utc))
    settlement_id = f"stl_{uuid4().hex}"
    reserve_account = await get_or_create_ledger_account(
        session,
        LedgerOwnerType.SYSTEM,
        settlement_id,
        created_at,
    )
    reservation = await post_ledger_transfer(
        session,
        source_account=merchant_account,
        destination_account=reserve_account,
        amount_atomic=settlement_amount,
        transaction_type=LedgerTransactionType.SETTLEMENT,
        idempotency_key=(
            f"settlement-reserve:{merchant.id}:{normalized_key}"
        ),
        reference_id=settlement_id,
        current_time=created_at,
    )
    settlement = Settlement(
        id=settlement_id,
        merchant_id=merchant.id,
        amount_atomic=settlement_amount,
        currency="USDC",
        chain="base-sepolia",
        destination_address=merchant.wallet_address,
        status=SettlementStatus.PENDING,
        idempotency_key=normalized_key,
        reservation_ledger_transaction_id=reservation.transaction.id,
        created_at=created_at,
    )
    session.add(settlement)
    await session.flush()
    return CreatedSettlement(settlement=settlement, replayed=False)


async def prepare_settlement_broadcast(
    session: AsyncSession,
    settlement_id: str,
    *,
    transaction_hash: str,
    transaction_nonce: int,
    signed_transaction: str,
    current_time: datetime | None = None,
) -> Settlement:
    """Persist a signed payout before allowing it to reach the network."""

    if TRANSACTION_HASH_PATTERN.fullmatch(transaction_hash) is None:
        raise SettlementError("Settlement transaction hash is invalid")
    if transaction_nonce < 0:
        raise SettlementError("Settlement transaction nonce cannot be negative")
    if (
        re.fullmatch(r"0x[0-9a-fA-F]+", signed_transaction) is None
        or len(signed_transaction) % 2 != 0
    ):
        raise SettlementError("Signed settlement transaction is invalid")

    settlement = await _locked_settlement(session, settlement_id)
    if settlement.status == SettlementStatus.BROADCASTING:
        if (
            settlement.transaction_hash.lower() != transaction_hash.lower()
            or settlement.transaction_nonce != transaction_nonce
            or settlement.signed_transaction != signed_transaction
        ):
            raise SettlementError(
                "Settlement already has different signed transaction data"
            )
        return settlement
    if settlement.status != SettlementStatus.PENDING:
        raise SettlementError(
            f"Cannot prepare a {settlement.status.value} settlement"
        )

    settlement.status = SettlementStatus.BROADCASTING
    settlement.transaction_hash = transaction_hash.lower()
    settlement.transaction_nonce = transaction_nonce
    settlement.signed_transaction = signed_transaction
    settlement.broadcast_at = _as_utc(current_time or datetime.now(timezone.utc))
    settlement.failure_reason = None
    await session.flush()
    return settlement


async def mark_settlement_submitted(
    session: AsyncSession,
    settlement_id: str,
    transaction_hash: str,
    current_time: datetime | None = None,
) -> Settlement:
    settlement = await _locked_settlement(session, settlement_id)
    if settlement.status == SettlementStatus.SUBMITTED:
        return settlement
    if settlement.status != SettlementStatus.BROADCASTING:
        raise SettlementError(
            f"Cannot submit a {settlement.status.value} settlement"
        )
    if (
        settlement.transaction_hash is None
        or settlement.transaction_hash.lower() != transaction_hash.lower()
    ):
        raise SettlementError("Broadcast transaction hash does not match settlement")

    settlement.status = SettlementStatus.SUBMITTED
    settlement.submitted_at = _as_utc(
        current_time or datetime.now(timezone.utc)
    )
    await session.flush()
    return settlement


async def confirm_settlement(
    session: AsyncSession,
    settlement_id: str,
    current_time: datetime | None = None,
) -> Settlement:
    """Finalize a proven payout and remove its value from custody liabilities."""

    settlement = await _locked_settlement(session, settlement_id)
    if settlement.status == SettlementStatus.CONFIRMED:
        return settlement
    if settlement.status not in {
        SettlementStatus.BROADCASTING,
        SettlementStatus.SUBMITTED,
    }:
        raise SettlementError(
            f"Cannot confirm a {settlement.status.value} settlement"
        )

    reserve = await _required_account(
        session, LedgerOwnerType.SYSTEM, settlement.id
    )
    if reserve.balance_atomic < settlement.amount_atomic:
        raise SettlementError("Settlement reserve balance is inconsistent")
    custody = await get_or_create_ledger_account(
        session,
        LedgerOwnerType.SYSTEM,
        SYSTEM_CUSTODY_OWNER_ID,
    )
    completed_at = _as_utc(current_time or datetime.now(timezone.utc))
    completion = await post_ledger_transfer(
        session,
        source_account=reserve,
        destination_account=custody,
        amount_atomic=settlement.amount_atomic,
        transaction_type=LedgerTransactionType.SETTLEMENT,
        idempotency_key=f"settlement-complete:{settlement.id}",
        reference_id=settlement.id,
        current_time=completed_at,
    )
    settlement.completion_ledger_transaction_id = completion.transaction.id
    settlement.status = SettlementStatus.CONFIRMED
    settlement.confirmed_at = completed_at
    settlement.signed_transaction = None
    settlement.failure_reason = None
    await session.flush()
    return settlement


async def fail_settlement(
    session: AsyncSession,
    settlement_id: str,
    reason: str,
    current_time: datetime | None = None,
) -> Settlement:
    """Release a reverted payout's reservation back to the merchant."""

    settlement = await _locked_settlement(session, settlement_id)
    if settlement.status == SettlementStatus.FAILED:
        return settlement
    if settlement.status not in {
        SettlementStatus.BROADCASTING,
        SettlementStatus.SUBMITTED,
    }:
        raise SettlementError(f"Cannot fail a {settlement.status.value} settlement")
    failed_at = _as_utc(current_time or datetime.now(timezone.utc))
    release = await _release_reserve(session, settlement, "failed", failed_at)
    settlement.completion_ledger_transaction_id = release.transaction.id
    settlement.status = SettlementStatus.FAILED
    settlement.failed_at = failed_at
    settlement.signed_transaction = None
    settlement.failure_reason = reason[:2000]
    await session.flush()
    return settlement


async def require_settlement_review(
    session: AsyncSession,
    settlement_id: str,
    reason: str,
) -> Settlement:
    """Freeze uncertain value for human review instead of guessing."""

    settlement = await _locked_settlement(session, settlement_id)
    if settlement.status == SettlementStatus.REVIEW_REQUIRED:
        return settlement
    if settlement.status not in {
        SettlementStatus.BROADCASTING,
        SettlementStatus.SUBMITTED,
    }:
        raise SettlementError(
            f"Cannot flag a {settlement.status.value} settlement for review"
        )
    settlement.status = SettlementStatus.REVIEW_REQUIRED
    settlement.signed_transaction = None
    settlement.failure_reason = reason[:2000]
    await session.flush()
    return settlement


async def cancel_settlement(
    session: AsyncSession,
    settlement_id: str,
    merchant_id: str,
    current_time: datetime | None = None,
) -> Settlement:
    """Cancel an unbroadcast settlement and restore available balance."""

    settlement = await _locked_settlement(session, settlement_id)
    if settlement.merchant_id != merchant_id:
        raise SettlementError("Settlement not found")
    if settlement.status == SettlementStatus.CANCELLED:
        return settlement
    if settlement.status != SettlementStatus.PENDING:
        raise SettlementError(
            "Only a pending, unbroadcast settlement can be cancelled"
        )
    cancelled_at = _as_utc(current_time or datetime.now(timezone.utc))
    release = await _release_reserve(
        session, settlement, "cancelled", cancelled_at
    )
    settlement.completion_ledger_transaction_id = release.transaction.id
    settlement.status = SettlementStatus.CANCELLED
    settlement.cancelled_at = cancelled_at
    await session.flush()
    return settlement


async def _release_reserve(
    session: AsyncSession,
    settlement: Settlement,
    outcome: str,
    current_time: datetime,
):
    reserve = await _required_account(
        session, LedgerOwnerType.SYSTEM, settlement.id
    )
    if reserve.balance_atomic < settlement.amount_atomic:
        raise SettlementError("Settlement reserve balance is inconsistent")
    merchant = await get_or_create_ledger_account(
        session,
        LedgerOwnerType.MERCHANT,
        settlement.merchant_id,
    )
    return await post_ledger_transfer(
        session,
        source_account=reserve,
        destination_account=merchant,
        amount_atomic=settlement.amount_atomic,
        transaction_type=LedgerTransactionType.SETTLEMENT,
        idempotency_key=f"settlement-{outcome}:{settlement.id}",
        reference_id=settlement.id,
        current_time=current_time,
    )


async def _required_account(
    session: AsyncSession,
    owner_type: LedgerOwnerType,
    owner_id: str,
) -> LedgerAccount:
    result = await session.execute(
        select(LedgerAccount).where(
            LedgerAccount.owner_type == owner_type,
            LedgerAccount.owner_id == owner_id,
            LedgerAccount.currency == "USDC",
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise SettlementError("Settlement ledger account is missing")
    return account


async def _locked_settlement(
    session: AsyncSession,
    settlement_id: str,
) -> Settlement:
    result = await session.execute(
        select(Settlement)
        .where(Settlement.id == settlement_id)
        .with_for_update()
    )
    settlement = result.scalar_one_or_none()
    if settlement is None:
        raise SettlementError("Settlement not found")
    return settlement


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
