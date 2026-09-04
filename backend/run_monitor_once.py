"""Run one StablePay blockchain reconciliation cycle from the command line."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from sqlalchemy.exc import SQLAlchemyError


APP_DIRECTORY = Path(__file__).resolve().parent / "app"
sys.path.insert(0, str(APP_DIRECTORY))

from blockchain.base import BaseSepoliaClient  # noqa: E402
from blockchain.base import BlockchainConnectionError  # noqa: E402
from blockchain.base import BlockchainTransactionError  # noqa: E402
from database.database import SessionLocal  # noqa: E402
from services.blockchain_monitor import BlockchainMonitorError  # noqa: E402
from services.blockchain_monitor import monitor_blockchain_once  # noqa: E402
from config import settings  # noqa: E402
from services.vault_monitor import monitor_vault_deposits_once  # noqa: E402
from services.settlement_monitor import refresh_submitted_settlements  # noqa: E402


async def run_once() -> int:
    async with BaseSepoliaClient.from_settings() as blockchain_client:
        async with SessionLocal() as session:
            try:
                result = await monitor_blockchain_once(
                    session,
                    blockchain_client,
                )
                vault_result = None
                if settings.stablepay_vault_address is not None:
                    vault_result = await monitor_vault_deposits_once(
                        session,
                        blockchain_client,
                    )
                    settlement_result = await refresh_submitted_settlements(
                        session,
                        blockchain_client,
                    )
                else:
                    settlement_result = None
            except (
                BlockchainConnectionError,
                BlockchainTransactionError,
                BlockchainMonitorError,
                SQLAlchemyError,
            ) as error:
                await session.rollback()
                print(f"Monitoring failed: {error}", file=sys.stderr)
                return 1

    print("Blockchain monitoring cycle complete")
    print("Latest block:", result.latest_block)
    print("Confirmation-safe tip:", result.confirmed_tip)
    if result.scanned_from is None:
        print("New block range scanned: none")
    else:
        print(
            "New block range scanned:",
            f"{result.scanned_from}-{result.scanned_to}",
        )
    print("USDC transfers seen:", result.transfers_seen)
    print("Payments matched:", result.payments_matched)
    print("Payments confirmed:", result.payments_confirmed)
    print("Ambiguous transfers:", result.ambiguous_transfers)
    if vault_result is None:
        print("Vault monitoring: disabled (STABLEPAY_VAULT_ADDRESS is unset)")
    else:
        print("Vault USDC transfers seen:", vault_result.transfers_seen)
        print("Vault deposits confirmed:", vault_result.deposits_confirmed)
        print("Ambiguous vault transfers:", vault_result.ambiguous_transfers)
    if settlement_result is not None:
        print("Settlements examined:", settlement_result.examined)
        print("Settlements confirmed:", settlement_result.confirmed)
        print("Settlements failed:", settlement_result.failed)
        print("Settlements needing review:", settlement_result.review_required)
    return 0


def main() -> int:
    return asyncio.run(run_once())


if __name__ == "__main__":
    raise SystemExit(main())
