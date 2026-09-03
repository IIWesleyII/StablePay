"""Background worker for automatic Base Sepolia payment reconciliation."""

import asyncio
import logging

from blockchain.base import BaseSepoliaClient
from config import settings
from database.database import SessionLocal
from services.blockchain_monitor import monitor_blockchain_once
from services.vault_monitor import monitor_vault_deposits_once


logger = logging.getLogger(__name__)


async def run_blockchain_monitor_worker(stop_event: asyncio.Event) -> None:
    """Continuously scan confirmed USDC blocks until application shutdown."""

    if not settings.blockchain_monitor_enabled:
        logger.warning(
            "Blockchain monitor disabled by BLOCKCHAIN_MONITOR_ENABLED"
        )
        return

    async with BaseSepoliaClient.from_settings() as blockchain_client:
        while not stop_event.is_set():
            result = None
            vault_result = None
            async with SessionLocal() as session:
                try:
                    result = await monitor_blockchain_once(
                        session,
                        blockchain_client,
                    )
                    if settings.stablepay_vault_address is not None:
                        vault_result = await monitor_vault_deposits_once(
                            session,
                            blockchain_client,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await session.rollback()
                    logger.exception("Blockchain monitoring cycle failed")
                else:
                    if result.transfers_seen or result.payments_confirmed:
                        logger.info(
                            "Blockchain scan %s-%s saw %s transfer(s), "
                            "matched %s payment(s), and confirmed %s payment(s)",
                            result.scanned_from,
                            result.scanned_to,
                            result.transfers_seen,
                            result.payments_matched,
                            result.payments_confirmed,
                        )
                    if result.ambiguous_transfers:
                        logger.warning(
                            "Blockchain scan found %s ambiguous transfer(s); "
                            "cursor retained for retry",
                            result.ambiguous_transfers,
                        )
                    if vault_result is not None and vault_result.deposits_confirmed:
                        logger.info(
                            "Vault scan confirmed %s deposit(s)",
                            vault_result.deposits_confirmed,
                        )
                    if vault_result is not None and vault_result.ambiguous_transfers:
                        logger.warning(
                            "Vault scan found %s ambiguous transfer(s); "
                            "cursor retained for retry",
                            vault_result.ambiguous_transfers,
                        )

            still_catching_up = (
                result is not None
                and result.scanned_to is not None
                and result.scanned_to < result.confirmed_tip
                and result.ambiguous_transfers == 0
            )
            vault_still_catching_up = (
                vault_result is not None
                and vault_result.scanned_to is not None
                and vault_result.scanned_to < vault_result.confirmed_tip
                and vault_result.ambiguous_transfers == 0
            )
            if still_catching_up or vault_still_catching_up:
                continue

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.blockchain_monitor_poll_seconds,
                )
            except TimeoutError:
                pass
