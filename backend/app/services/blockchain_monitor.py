"""Durable Base Sepolia USDC scanning and payment reconciliation."""

import logging
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blockchain.base import BaseSepoliaClient
from blockchain.base import BlockchainConnectionError
from blockchain.base import BlockchainTransactionError
from blockchain.base import UsdcTransfer
from config import settings
from database.models import BlockchainCursor
from database.models import Payment
from domain.payments import PaymentStatus
from services.payment_lifecycle import mark_payment_confirmed
from services.payment_lifecycle import mark_payment_detected
from services.payment_lifecycle import restore_expired_payment_from_on_time_transfer
from services.payment_verification import PaymentVerificationError
from services.payment_verification import find_matching_transfer
from services.webhook_events import create_payment_confirmed_event
from services.webhook_events import get_payment_webhook_url


logger = logging.getLogger(__name__)

BASE_SEPOLIA_USDC_CURSOR_ID = "base-sepolia-usdc"
BASE_SEPOLIA_CHAIN = "base-sepolia"


class BlockchainMonitorError(RuntimeError):
    """Raised when durable scanner state cannot be safely used."""


@dataclass(frozen=True)
class BlockchainMonitorResult:
    """A compact summary of one monitoring cycle."""

    latest_block: int
    confirmed_tip: int
    scanned_from: int | None
    scanned_to: int | None
    transfers_seen: int
    payments_matched: int
    payments_confirmed: int
    ambiguous_transfers: int


async def monitor_blockchain_once(
    session: AsyncSession,
    blockchain_client: BaseSepoliaClient,
    current_time: datetime | None = None,
) -> BlockchainMonitorResult:
    """Scan one safe block batch, reconcile it, and refresh confirmations."""

    scan_time = _as_utc(current_time or datetime.now(timezone.utc))
    latest_block = await blockchain_client.get_latest_block_number()
    confirmed_tip = latest_block - settings.payment_required_confirmations + 1

    confirmed_count = await refresh_confirming_payments(
        session,
        blockchain_client,
        scan_time,
        settings.blockchain_monitor_confirmation_batch_size,
    )

    if confirmed_tip < 0:
        await session.commit()
        return BlockchainMonitorResult(
            latest_block=latest_block,
            confirmed_tip=confirmed_tip,
            scanned_from=None,
            scanned_to=None,
            transfers_seen=0,
            payments_matched=0,
            payments_confirmed=confirmed_count,
            ambiguous_transfers=0,
        )

    cursor = await _get_or_create_cursor(session, confirmed_tip, scan_time)
    if (
        cursor.last_scanned_block >= 0
        and cursor.last_scanned_block_hash is not None
    ):
        canonical_cursor_hash = await blockchain_client.get_block_hash(
            cursor.last_scanned_block
        )
        if canonical_cursor_hash.lower() != cursor.last_scanned_block_hash.lower():
            raise BlockchainMonitorError(
                "Blockchain cursor block hash no longer matches the canonical chain"
            )

    from_block = cursor.last_scanned_block + 1
    if from_block > confirmed_tip:
        await session.commit()
        return BlockchainMonitorResult(
            latest_block=latest_block,
            confirmed_tip=confirmed_tip,
            scanned_from=None,
            scanned_to=None,
            transfers_seen=0,
            payments_matched=0,
            payments_confirmed=confirmed_count,
            ambiguous_transfers=0,
        )

    to_block = min(
        confirmed_tip,
        from_block + settings.blockchain_monitor_block_batch_size - 1,
    )
    recipients_result = await session.execute(
        select(Payment.recipient_address)
        .where(Payment.status.in_([PaymentStatus.PENDING, PaymentStatus.EXPIRED]))
        .distinct()
    )
    recipient_addresses = list(recipients_result.scalars())
    transfers = await blockchain_client.get_usdc_transfer_logs(
        from_block,
        to_block,
        recipient_addresses,
        latest_block=latest_block,
    )

    matched_count = 0
    scan_confirmed_count = 0
    ambiguous_blocks: list[int] = []
    for transfer in transfers:
        resolution = await reconcile_usdc_transfer(
            session,
            transfer,
            scan_time,
        )
        if resolution == "matched":
            matched_count += 1
            if transfer.confirmations >= settings.payment_required_confirmations:
                scan_confirmed_count += 1
        elif resolution == "ambiguous":
            ambiguous_blocks.append(transfer.block_number)

    advance_to = to_block
    if ambiguous_blocks:
        advance_to = min(ambiguous_blocks) - 1

    if advance_to >= from_block:
        cursor.last_scanned_block = advance_to
        cursor.last_scanned_block_hash = await blockchain_client.get_block_hash(
            advance_to
        )
        cursor.updated_at = scan_time

    await session.commit()
    return BlockchainMonitorResult(
        latest_block=latest_block,
        confirmed_tip=confirmed_tip,
        scanned_from=from_block,
        scanned_to=to_block,
        transfers_seen=len(transfers),
        payments_matched=matched_count,
        payments_confirmed=confirmed_count + scan_confirmed_count,
        ambiguous_transfers=len(ambiguous_blocks),
    )


async def reconcile_usdc_transfer(
    session: AsyncSession,
    transfer: UsdcTransfer,
    detected_at: datetime | None = None,
) -> str:
    """Match one confirmed transfer without guessing between candidates."""

    if transfer.block_timestamp is None:
        raise BlockchainMonitorError(
            "Scanned USDC transfer must include its block timestamp"
        )
    detection_time = _as_utc(detected_at or datetime.now(timezone.utc))
    transfer_time = _as_utc(transfer.block_timestamp)

    existing_result = await session.execute(
        select(Payment)
        .where(Payment.transaction_hash == transfer.transaction_hash)
        .with_for_update()
    )
    existing_payment = existing_result.scalar_one_or_none()
    if existing_payment is not None:
        try:
            find_matching_transfer(existing_payment, [transfer])
        except PaymentVerificationError:
            return "ignored"
        _record_transfer_metadata(existing_payment, transfer)
        if (
            existing_payment.status == PaymentStatus.CONFIRMING
            and transfer.confirmations >= settings.payment_required_confirmations
        ):
            await _confirm_payment(session, existing_payment, detection_time)
        return "already_matched"

    candidates_result = await session.execute(
        select(Payment)
        .where(
            Payment.status.in_([PaymentStatus.PENDING, PaymentStatus.EXPIRED]),
            func.lower(Payment.recipient_address) == transfer.recipient.lower(),
            Payment.amount == transfer.amount,
        )
        .order_by(Payment.created_at, Payment.id)
        .with_for_update()
    )
    candidates = [
        payment
        for payment in candidates_result.scalars()
        if _as_utc(payment.created_at) <= transfer_time <= _as_utc(payment.expires_at)
    ]

    if not candidates:
        return "ignored"
    if len(candidates) > 1:
        logger.warning(
            "USDC transfer %s is ambiguous across %s payments",
            transfer.transaction_hash,
            len(candidates),
        )
        return "ambiguous"

    payment = candidates[0]
    if payment.status == PaymentStatus.EXPIRED:
        restore_expired_payment_from_on_time_transfer(
            payment,
            transfer.transaction_hash,
            transferred_at=transfer_time,
            detected_at=detection_time,
        )
    else:
        mark_payment_detected(
            payment,
            transfer.transaction_hash,
            detection_time,
        )
    _record_transfer_metadata(payment, transfer)

    if transfer.confirmations >= settings.payment_required_confirmations:
        await _confirm_payment(session, payment, detection_time)
    return "matched"


async def refresh_confirming_payments(
    session: AsyncSession,
    blockchain_client: BaseSepoliaClient,
    current_time: datetime | None = None,
    batch_size: int = 100,
) -> int:
    """Advance manually detected payments once their receipts are confirmed."""

    if batch_size <= 0:
        raise ValueError("Confirmation batch size must be positive")
    confirmation_time = _as_utc(current_time or datetime.now(timezone.utc))
    result = await session.execute(
        select(Payment)
        .where(Payment.status == PaymentStatus.CONFIRMING)
        .order_by(Payment.detected_at, Payment.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    payments = list(result.scalars())
    confirmed_count = 0

    for payment in payments:
        if payment.transaction_hash is None:
            logger.error("Confirming payment %s has no transaction hash", payment.id)
            continue
        try:
            transfers = await blockchain_client.get_usdc_transfers(
                payment.transaction_hash
            )
            transfer = find_matching_transfer(payment, transfers)
        except BlockchainConnectionError:
            raise
        except (BlockchainTransactionError, PaymentVerificationError) as error:
            logger.warning(
                "Could not refresh confirming payment %s: %s",
                payment.id,
                error,
            )
            continue

        _record_transfer_metadata(payment, transfer)
        if transfer.confirmations >= settings.payment_required_confirmations:
            await _confirm_payment(session, payment, confirmation_time)
            confirmed_count += 1

    return confirmed_count


async def _get_or_create_cursor(
    session: AsyncSession,
    confirmed_tip: int,
    current_time: datetime,
) -> BlockchainCursor:
    cursor = await session.get(
        BlockchainCursor,
        BASE_SEPOLIA_USDC_CURSOR_ID,
        with_for_update=True,
    )
    if cursor is not None:
        if (
            cursor.chain != BASE_SEPOLIA_CHAIN
            or cursor.token_address.lower()
            != settings.base_sepolia_usdc_address.lower()
        ):
            raise BlockchainMonitorError(
                "Blockchain cursor does not match the configured chain and token"
            )
        return cursor

    first_block = max(
        0,
        confirmed_tip - settings.blockchain_monitor_initial_lookback_blocks + 1,
    )
    cursor = BlockchainCursor(
        id=BASE_SEPOLIA_USDC_CURSOR_ID,
        chain=BASE_SEPOLIA_CHAIN,
        token_address=settings.base_sepolia_usdc_address,
        last_scanned_block=first_block - 1,
        last_scanned_block_hash=None,
        updated_at=current_time,
    )
    session.add(cursor)
    await session.flush()
    return cursor


async def _confirm_payment(
    session: AsyncSession,
    payment: Payment,
    confirmed_at: datetime,
) -> None:
    mark_payment_confirmed(payment, confirmed_at)
    webhook_url = await get_payment_webhook_url(payment, session)
    session.add(
        create_payment_confirmed_event(
            payment,
            webhook_url,
            confirmed_at,
        )
    )


def _record_transfer_metadata(payment: Payment, transfer: UsdcTransfer) -> None:
    payment.payer_address = transfer.sender
    payment.transaction_block_number = transfer.block_number
    payment.transaction_log_index = transfer.log_index


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
