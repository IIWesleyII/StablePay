from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from blockchain.base import UsdcTransfer
from config import settings
from database.models import BlockchainCursor
from database.models import Merchant
from database.models import Payment
from database.models import WebhookEvent
from domain.payments import PaymentStatus
from services.blockchain_monitor import BASE_SEPOLIA_USDC_CURSOR_ID
from services.blockchain_monitor import BlockchainMonitorError
from services.blockchain_monitor import monitor_blockchain_once
from services.blockchain_monitor import refresh_confirming_payments
from services.payment_lifecycle import mark_payment_detected
from services.payment_lifecycle import mark_payment_expired


CURRENT_TIME = datetime(2026, 9, 3, 12, 10, tzinfo=timezone.utc)
CREATED_AT = CURRENT_TIME - timedelta(minutes=10)
EXPIRES_AT = CURRENT_TIME + timedelta(minutes=5)
TRANSFER_TIME = CREATED_AT + timedelta(minutes=5)
TRANSACTION_HASH = "0x" + "ab" * 32
SENDER = "0x1111111111111111111111111111111111111111"


class FakeMonitorClient:
    def __init__(
        self,
        transfers: list[UsdcTransfer] | None = None,
        latest_block: int = 110,
    ) -> None:
        self.latest_block = latest_block
        self.transfers = transfers or []
        self.scan_calls: list[tuple[int, int, list[str], int | None]] = []
        self.receipt_transfers: dict[str, list[UsdcTransfer]] = {}

    async def get_latest_block_number(self) -> int:
        return self.latest_block

    async def get_usdc_transfer_logs(
        self,
        from_block: int,
        to_block: int,
        recipient_addresses: list[str],
        latest_block: int | None = None,
    ) -> list[UsdcTransfer]:
        self.scan_calls.append(
            (from_block, to_block, recipient_addresses, latest_block)
        )
        return [
            transfer
            for transfer in self.transfers
            if from_block <= transfer.block_number <= to_block
            and transfer.recipient in recipient_addresses
        ]

    async def get_block_hash(self, block_number: int) -> str:
        return "0x" + f"{block_number:064x}"

    async def get_usdc_transfers(
        self,
        transaction_hash: str,
    ) -> list[UsdcTransfer]:
        return self.receipt_transfers.get(transaction_hash, [])


def make_payment(
    merchant: Merchant,
    payment_id: str = "pay_monitor_test",
    amount: str = "0.01",
    status: PaymentStatus = PaymentStatus.PENDING,
    created_at: datetime = CREATED_AT,
    expires_at: datetime = EXPIRES_AT,
) -> Payment:
    return Payment(
        id=payment_id,
        merchant_id=merchant.id,
        amount=Decimal(amount),
        currency="USDC",
        chain="base-sepolia",
        recipient_address=merchant.wallet_address,
        status=status,
        created_at=created_at,
        expires_at=expires_at,
    )


def make_transfer(
    merchant: Merchant,
    transaction_hash: str = TRANSACTION_HASH,
    amount: str = "0.01",
    confirmations: int = 11,
    block_number: int = 100,
    block_timestamp: datetime = TRANSFER_TIME,
) -> UsdcTransfer:
    decimal_amount = Decimal(amount)
    return UsdcTransfer(
        transaction_hash=transaction_hash,
        log_index=2,
        sender=SENDER,
        recipient=merchant.wallet_address,
        raw_amount=int(decimal_amount * Decimal(1_000_000)),
        amount=decimal_amount,
        block_number=block_number,
        confirmations=confirmations,
        block_hash="0x" + "cd" * 32,
        block_timestamp=block_timestamp,
    )


@pytest.mark.asyncio
async def test_monitor_matches_and_confirms_unique_payment(
    test_session: AsyncSession,
    authenticated_merchant: Merchant,
    monkeypatch,
):
    monkeypatch.setattr(settings, "blockchain_monitor_initial_lookback_blocks", 10)
    payment = make_payment(authenticated_merchant)
    transfer = make_transfer(authenticated_merchant)
    test_session.add(payment)
    await test_session.commit()
    client = FakeMonitorClient([transfer])

    result = await monitor_blockchain_once(test_session, client, CURRENT_TIME)

    await test_session.refresh(payment)
    assert payment.status is PaymentStatus.CONFIRMED
    assert payment.transaction_hash == TRANSACTION_HASH
    assert payment.payer_address == SENDER
    assert payment.transaction_block_number == 100
    assert payment.transaction_log_index == 2
    assert result.scanned_from == 99
    assert result.scanned_to == 108
    assert result.transfers_seen == 1
    assert result.payments_matched == 1
    assert result.payments_confirmed == 1

    cursor = await test_session.get(
        BlockchainCursor,
        BASE_SEPOLIA_USDC_CURSOR_ID,
    )
    assert cursor.last_scanned_block == 108
    assert cursor.last_scanned_block_hash == "0x" + f"{108:064x}"

    event_result = await test_session.execute(
        select(WebhookEvent).where(WebhookEvent.payment_id == payment.id)
    )
    assert event_result.scalar_one().event_type == "payment.confirmed"


@pytest.mark.asyncio
async def test_monitor_resumes_after_durable_cursor(
    test_session: AsyncSession,
    authenticated_merchant: Merchant,
):
    cursor = BlockchainCursor(
        id=BASE_SEPOLIA_USDC_CURSOR_ID,
        chain="base-sepolia",
        token_address=settings.base_sepolia_usdc_address,
        last_scanned_block=105,
        last_scanned_block_hash="0x" + f"{105:064x}",
        updated_at=CURRENT_TIME,
    )
    test_session.add_all([cursor, make_payment(authenticated_merchant)])
    await test_session.commit()
    client = FakeMonitorClient()

    result = await monitor_blockchain_once(test_session, client, CURRENT_TIME)

    assert result.scanned_from == 106
    assert result.scanned_to == 108
    assert client.scan_calls[0][0:2] == (106, 108)
    await test_session.refresh(cursor)
    assert cursor.last_scanned_block == 108


@pytest.mark.asyncio
async def test_monitor_advances_cursor_when_no_open_payments(
    test_session: AsyncSession,
    monkeypatch,
):
    monkeypatch.setattr(settings, "blockchain_monitor_initial_lookback_blocks", 5)
    client = FakeMonitorClient()

    result = await monitor_blockchain_once(test_session, client, CURRENT_TIME)

    assert result.scanned_from == 104
    assert result.scanned_to == 108
    assert client.scan_calls[0][2] == []
    cursor = await test_session.get(
        BlockchainCursor,
        BASE_SEPOLIA_USDC_CURSOR_ID,
    )
    assert cursor.last_scanned_block == 108


@pytest.mark.asyncio
async def test_monitor_does_not_guess_between_ambiguous_payments(
    test_session: AsyncSession,
    authenticated_merchant: Merchant,
    monkeypatch,
):
    monkeypatch.setattr(settings, "blockchain_monitor_initial_lookback_blocks", 10)
    first = make_payment(authenticated_merchant, "pay_ambiguous_first")
    second = make_payment(authenticated_merchant, "pay_ambiguous_second")
    test_session.add_all([first, second])
    await test_session.commit()
    client = FakeMonitorClient([make_transfer(authenticated_merchant)])

    result = await monitor_blockchain_once(test_session, client, CURRENT_TIME)

    assert first.status is PaymentStatus.PENDING
    assert second.status is PaymentStatus.PENDING
    assert result.ambiguous_transfers == 1
    cursor = await test_session.get(
        BlockchainCursor,
        BASE_SEPOLIA_USDC_CURSOR_ID,
    )
    assert cursor.last_scanned_block == 99


@pytest.mark.asyncio
async def test_monitor_restores_expired_payment_paid_on_time(
    test_session: AsyncSession,
    authenticated_merchant: Merchant,
    monkeypatch,
):
    monkeypatch.setattr(settings, "blockchain_monitor_initial_lookback_blocks", 10)
    payment = make_payment(authenticated_merchant)
    test_session.add(payment)
    await test_session.commit()
    mark_payment_expired(payment, EXPIRES_AT)
    await test_session.commit()

    client = FakeMonitorClient([make_transfer(authenticated_merchant)])
    await monitor_blockchain_once(
        test_session,
        client,
        EXPIRES_AT + timedelta(minutes=2),
    )

    await test_session.refresh(payment)
    assert payment.status is PaymentStatus.CONFIRMED
    assert payment.transaction_hash == TRANSACTION_HASH


@pytest.mark.asyncio
async def test_monitor_ignores_transfer_mined_after_expiration(
    test_session: AsyncSession,
    authenticated_merchant: Merchant,
    monkeypatch,
):
    monkeypatch.setattr(settings, "blockchain_monitor_initial_lookback_blocks", 10)
    payment = make_payment(authenticated_merchant)
    test_session.add(payment)
    await test_session.commit()
    transfer = make_transfer(
        authenticated_merchant,
        block_timestamp=EXPIRES_AT + timedelta(seconds=1),
    )

    result = await monitor_blockchain_once(
        test_session,
        FakeMonitorClient([transfer]),
        EXPIRES_AT + timedelta(minutes=2),
    )

    assert payment.status is PaymentStatus.PENDING
    assert payment.transaction_hash is None
    assert result.payments_matched == 0


@pytest.mark.asyncio
async def test_monitor_refreshes_manually_detected_payment(
    test_session: AsyncSession,
    authenticated_merchant: Merchant,
):
    payment = make_payment(authenticated_merchant)
    mark_payment_detected(payment, TRANSACTION_HASH, CURRENT_TIME)
    test_session.add(payment)
    await test_session.commit()
    transfer = make_transfer(authenticated_merchant, confirmations=3)
    client = FakeMonitorClient()
    client.receipt_transfers[TRANSACTION_HASH] = [transfer]

    confirmed_count = await refresh_confirming_payments(
        test_session,
        client,
        CURRENT_TIME + timedelta(seconds=10),
    )
    await test_session.commit()

    assert confirmed_count == 1
    assert payment.status is PaymentStatus.CONFIRMED
    event_result = await test_session.execute(
        select(WebhookEvent).where(WebhookEvent.payment_id == payment.id)
    )
    assert event_result.scalar_one() is not None


@pytest.mark.asyncio
async def test_monitor_leaves_payment_confirming_below_threshold(
    test_session: AsyncSession,
    authenticated_merchant: Merchant,
):
    payment = make_payment(authenticated_merchant)
    mark_payment_detected(payment, TRANSACTION_HASH, CURRENT_TIME)
    test_session.add(payment)
    await test_session.commit()
    transfer = make_transfer(authenticated_merchant, confirmations=2)
    client = FakeMonitorClient()
    client.receipt_transfers[TRANSACTION_HASH] = [transfer]

    confirmed_count = await refresh_confirming_payments(
        test_session,
        client,
        CURRENT_TIME + timedelta(seconds=10),
    )

    assert confirmed_count == 0
    assert payment.status is PaymentStatus.CONFIRMING


@pytest.mark.asyncio
async def test_monitor_rejects_cursor_for_different_token(
    test_session: AsyncSession,
):
    cursor = BlockchainCursor(
        id=BASE_SEPOLIA_USDC_CURSOR_ID,
        chain="base-sepolia",
        token_address="0x9999999999999999999999999999999999999999",
        last_scanned_block=100,
        updated_at=CURRENT_TIME,
    )
    test_session.add(cursor)
    await test_session.commit()

    with pytest.raises(BlockchainMonitorError, match="does not match"):
        await monitor_blockchain_once(
            test_session,
            FakeMonitorClient(),
            CURRENT_TIME,
        )


@pytest.mark.asyncio
async def test_monitor_stops_when_cursor_block_was_reorganized(
    test_session: AsyncSession,
):
    cursor = BlockchainCursor(
        id=BASE_SEPOLIA_USDC_CURSOR_ID,
        chain="base-sepolia",
        token_address=settings.base_sepolia_usdc_address,
        last_scanned_block=100,
        last_scanned_block_hash="0x" + "ff" * 32,
        updated_at=CURRENT_TIME,
    )
    test_session.add(cursor)
    await test_session.commit()

    with pytest.raises(BlockchainMonitorError, match="canonical chain"):
        await monitor_blockchain_once(
            test_session,
            FakeMonitorClient(),
            CURRENT_TIME,
        )

    await test_session.refresh(cursor)
    assert cursor.last_scanned_block == 100
