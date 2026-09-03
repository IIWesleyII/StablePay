"""Automatic Base Sepolia reconciliation for StablePay vault deposits."""

import logging
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blockchain.base import BaseSepoliaClient
from blockchain.base import UsdcTransfer
from config import settings
from database.models import BlockchainCursor
from database.models import Vault
from database.models import VaultDeposit
from domain.ledger import LedgerOwnerType
from domain.ledger import LedgerTransactionType
from domain.ledger import VaultDepositStatus
from services.blockchain_monitor import BASE_SEPOLIA_CHAIN
from services.blockchain_monitor import BlockchainMonitorError
from services.ledger import SYSTEM_CUSTODY_OWNER_ID
from services.ledger import get_or_create_ledger_account
from services.ledger import post_ledger_transfer


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VaultMonitorResult:
    latest_block: int
    confirmed_tip: int
    scanned_from: int | None
    scanned_to: int | None
    transfers_seen: int
    deposits_confirmed: int
    ambiguous_transfers: int


async def monitor_vault_deposits_once(
    session: AsyncSession,
    blockchain_client: BaseSepoliaClient,
    current_time: datetime | None = None,
) -> VaultMonitorResult:
    """Scan one confirmed block batch and credit exact deposit matches."""

    if settings.stablepay_vault_address is None:
        raise BlockchainMonitorError("STABLEPAY_VAULT_ADDRESS is not configured")

    scan_time = _as_utc(current_time or datetime.now(timezone.utc))
    latest_block = await blockchain_client.get_latest_block_number()
    confirmed_tip = latest_block - settings.payment_required_confirmations + 1
    if confirmed_tip < 0:
        return VaultMonitorResult(
            latest_block=latest_block,
            confirmed_tip=confirmed_tip,
            scanned_from=None,
            scanned_to=None,
            transfers_seen=0,
            deposits_confirmed=0,
            ambiguous_transfers=0,
        )

    cursor = await _get_or_create_cursor(session, confirmed_tip, scan_time)
    if cursor.last_scanned_block >= 0 and cursor.last_scanned_block_hash is not None:
        canonical_hash = await blockchain_client.get_block_hash(
            cursor.last_scanned_block
        )
        if canonical_hash.lower() != cursor.last_scanned_block_hash.lower():
            raise BlockchainMonitorError(
                "Vault cursor block hash no longer matches the canonical chain"
            )

    from_block = cursor.last_scanned_block + 1
    if from_block > confirmed_tip:
        await session.commit()
        return VaultMonitorResult(
            latest_block=latest_block,
            confirmed_tip=confirmed_tip,
            scanned_from=None,
            scanned_to=None,
            transfers_seen=0,
            deposits_confirmed=0,
            ambiguous_transfers=0,
        )

    to_block = min(
        confirmed_tip,
        from_block + settings.blockchain_monitor_block_batch_size - 1,
    )
    transfers = await blockchain_client.get_usdc_transfer_logs(
        from_block,
        to_block,
        [settings.stablepay_vault_address],
        latest_block=latest_block,
    )

    confirmed_count = 0
    ambiguous_blocks: list[int] = []
    for transfer in transfers:
        resolution = await reconcile_vault_transfer(session, transfer, scan_time)
        if resolution == "matched":
            confirmed_count += 1
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
    return VaultMonitorResult(
        latest_block=latest_block,
        confirmed_tip=confirmed_tip,
        scanned_from=from_block,
        scanned_to=to_block,
        transfers_seen=len(transfers),
        deposits_confirmed=confirmed_count,
        ambiguous_transfers=len(ambiguous_blocks),
    )


async def reconcile_vault_transfer(
    session: AsyncSession,
    transfer: UsdcTransfer,
    detected_at: datetime | None = None,
) -> str:
    """Credit one exact sender/amount/time match, without guessing."""

    if transfer.block_timestamp is None:
        raise BlockchainMonitorError(
            "Scanned vault transfer must include its block timestamp"
        )
    if settings.stablepay_vault_address is None:
        raise BlockchainMonitorError("STABLEPAY_VAULT_ADDRESS is not configured")
    if transfer.recipient.lower() != settings.stablepay_vault_address.lower():
        return "ignored"
    if transfer.confirmations < settings.payment_required_confirmations:
        return "ignored"

    existing_result = await session.execute(
        select(VaultDeposit).where(
            VaultDeposit.transaction_hash == transfer.transaction_hash,
            VaultDeposit.transaction_log_index == transfer.log_index,
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        return "already_matched"

    transfer_time = _as_utc(transfer.block_timestamp)
    candidates_result = await session.execute(
        select(VaultDeposit, Vault)
        .join(Vault, Vault.id == VaultDeposit.vault_id)
        .where(
            VaultDeposit.status.in_(
                [VaultDepositStatus.PENDING, VaultDepositStatus.EXPIRED]
            ),
            VaultDeposit.amount_atomic == transfer.raw_amount,
            func.lower(Vault.wallet_address) == transfer.sender.lower(),
        )
        .order_by(VaultDeposit.created_at, VaultDeposit.id)
        .with_for_update()
    )
    candidates = [
        (deposit, vault)
        for deposit, vault in candidates_result
        if _as_utc(deposit.created_at)
        <= transfer_time
        <= _as_utc(deposit.expires_at)
    ]
    if not candidates:
        return "ignored"
    if len(candidates) > 1:
        logger.warning(
            "USDC vault transfer %s is ambiguous across %s deposits",
            transfer.transaction_hash,
            len(candidates),
        )
        return "ambiguous"

    deposit, vault = candidates[0]
    system_account = await get_or_create_ledger_account(
        session,
        LedgerOwnerType.SYSTEM,
        SYSTEM_CUSTODY_OWNER_ID,
    )
    vault_account = await get_or_create_ledger_account(
        session,
        LedgerOwnerType.VAULT,
        vault.id,
    )
    await post_ledger_transfer(
        session,
        source_account=system_account,
        destination_account=vault_account,
        amount_atomic=transfer.raw_amount,
        transaction_type=LedgerTransactionType.DEPOSIT,
        idempotency_key=(
            f"deposit:{transfer.transaction_hash}:{transfer.log_index}"
        ),
        reference_id=deposit.id,
        current_time=_as_utc(detected_at or datetime.now(timezone.utc)),
    )
    deposit.status = VaultDepositStatus.CONFIRMED
    deposit.transaction_hash = transfer.transaction_hash
    deposit.transaction_block_number = transfer.block_number
    deposit.transaction_log_index = transfer.log_index
    deposit.confirmed_at = _as_utc(detected_at or datetime.now(timezone.utc))
    return "matched"


async def _get_or_create_cursor(
    session: AsyncSession,
    confirmed_tip: int,
    current_time: datetime,
) -> BlockchainCursor:
    assert settings.stablepay_vault_address is not None
    cursor_id = f"vault-usdc-{settings.stablepay_vault_address.lower()}"
    cursor = await session.get(BlockchainCursor, cursor_id, with_for_update=True)
    if cursor is not None:
        if (
            cursor.chain != BASE_SEPOLIA_CHAIN
            or cursor.token_address.lower()
            != settings.base_sepolia_usdc_address.lower()
        ):
            raise BlockchainMonitorError(
                "Vault cursor does not match the configured chain and token"
            )
        return cursor

    first_block = max(
        0,
        confirmed_tip - settings.blockchain_monitor_initial_lookback_blocks + 1,
    )
    cursor = BlockchainCursor(
        id=cursor_id,
        chain=BASE_SEPOLIA_CHAIN,
        token_address=settings.base_sepolia_usdc_address,
        last_scanned_block=first_block - 1,
        last_scanned_block_hash=None,
        updated_at=current_time,
    )
    session.add(cursor)
    await session.flush()
    return cursor


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
