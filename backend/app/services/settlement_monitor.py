"""Read-only blockchain confirmation and finalization for settlements."""

import logging
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blockchain.base import BaseSepoliaClient
from blockchain.base import BlockchainConnectionError
from blockchain.base import BlockchainTransactionError
from blockchain.base import TransactionFailedError
from blockchain.base import TransactionNotMinedError
from config import settings
from database.models import Settlement
from domain.settlements import SettlementStatus
from services.settlements import confirm_settlement
from services.settlements import fail_settlement
from services.settlements import mark_settlement_submitted
from services.settlements import require_settlement_review


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettlementMonitorResult:
    examined: int
    awaiting_receipt: int
    awaiting_confirmations: int
    confirmed: int
    failed: int
    review_required: int


async def refresh_submitted_settlements(
    session: AsyncSession,
    blockchain_client: BaseSepoliaClient,
    current_time: datetime | None = None,
    batch_size: int | None = None,
) -> SettlementMonitorResult:
    """Verify payout receipts and finish their reserved ledger movements."""

    if settings.stablepay_vault_address is None:
        return SettlementMonitorResult(0, 0, 0, 0, 0, 0)
    limit = batch_size or settings.settlement_confirmation_batch_size
    if limit <= 0:
        raise ValueError("Settlement confirmation batch size must be positive")
    checked_at = _as_utc(current_time or datetime.now(timezone.utc))
    result = await session.execute(
        select(Settlement)
        .where(
            Settlement.status.in_(
                [SettlementStatus.BROADCASTING, SettlementStatus.SUBMITTED]
            )
        )
        .order_by(Settlement.broadcast_at, Settlement.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    settlements = list(result.scalars())
    awaiting_receipt = 0
    awaiting_confirmations = 0
    confirmed = 0
    failed = 0
    review_required = 0

    for settlement in settlements:
        if settlement.transaction_hash is None:
            await require_settlement_review(
                session,
                settlement.id,
                "Settlement has no expected transaction hash",
            )
            review_required += 1
            continue
        try:
            transfers = await blockchain_client.get_usdc_transfers(
                settlement.transaction_hash
            )
        except TransactionNotMinedError:
            awaiting_receipt += 1
            continue
        except TransactionFailedError as error:
            await fail_settlement(session, settlement.id, str(error), checked_at)
            failed += 1
            continue
        except BlockchainConnectionError:
            raise
        except BlockchainTransactionError as error:
            await require_settlement_review(session, settlement.id, str(error))
            review_required += 1
            continue

        matching = [
            transfer
            for transfer in transfers
            if transfer.sender.lower()
            == settings.stablepay_vault_address.lower()
            and transfer.recipient.lower()
            == settlement.destination_address.lower()
            and transfer.raw_amount == settlement.amount_atomic
        ]
        if len(matching) != 1:
            await require_settlement_review(
                session,
                settlement.id,
                "Mined transaction did not contain exactly one expected USDC transfer",
            )
            review_required += 1
            continue

        transfer = matching[0]
        if settlement.status == SettlementStatus.BROADCASTING:
            await mark_settlement_submitted(
                session,
                settlement.id,
                settlement.transaction_hash,
                checked_at,
            )
        if transfer.confirmations < settings.payment_required_confirmations:
            awaiting_confirmations += 1
            continue

        await confirm_settlement(session, settlement.id, checked_at)
        confirmed += 1

    await session.commit()
    return SettlementMonitorResult(
        examined=len(settlements),
        awaiting_receipt=awaiting_receipt,
        awaiting_confirmations=awaiting_confirmations,
        confirmed=confirmed,
        failed=failed,
        review_required=review_required,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
