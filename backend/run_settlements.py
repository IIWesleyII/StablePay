"""Sign and broadcast pending Base Sepolia merchant settlements."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from decimal import Decimal
from pathlib import Path

from eth_account import Account
from hexbytes import HexBytes
from sqlalchemy import select
from web3 import Web3
from web3.exceptions import TransactionNotFound


APP_DIRECTORY = Path(__file__).resolve().parent / "app"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIRECTORY))
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402
from database.database import SessionLocal  # noqa: E402
from database.models import Settlement  # noqa: E402
from domain.settlements import SettlementStatus  # noqa: E402
from services.settlements import SettlementError  # noqa: E402
from services.settlements import mark_settlement_submitted  # noqa: E402
from services.settlements import prepare_settlement_broadcast  # noqa: E402
from backend.send_test_payment import ERC20_ABI  # noqa: E402
from backend.send_test_payment import PaymentScriptError  # noqa: E402
from backend.send_test_payment import build_transfer_transaction  # noqa: E402
from backend.send_test_payment import connect_to_base_sepolia  # noqa: E402


USDC_SCALE = Decimal(1_000_000)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Broadcast aggregate merchant payouts on Base Sepolia",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum settlements to process (default: 20)",
    )
    parser.add_argument(
        "--private-key-env",
        default="STABLEPAY_VAULT_PRIVATE_KEY",
        help="Environment variable containing the vault wallet private key",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation for newly signed payouts",
    )
    return parser.parse_args()


def load_vault_account(environment_variable: str):
    private_key = os.environ.get(environment_variable, "").strip()
    if not private_key:
        private_key = getpass.getpass("Vault wallet private key (hidden): ").strip()
    try:
        return Account.from_key(private_key)
    except (TypeError, ValueError) as error:
        raise PaymentScriptError("Vault private key is invalid") from error
    finally:
        private_key = ""


async def load_candidates(limit: int) -> list[Settlement]:
    if limit <= 0 or limit > 100:
        raise PaymentScriptError("Settlement limit must be between 1 and 100")
    async with SessionLocal() as session:
        result = await session.execute(
            select(Settlement)
            .where(
                Settlement.status.in_(
                    [SettlementStatus.BROADCASTING, SettlementStatus.PENDING]
                )
            )
            .order_by(Settlement.created_at, Settlement.id)
            .limit(limit)
        )
        return list(result.scalars())


async def save_prepared_transaction(
    settlement_id: str,
    transaction_hash: str,
    nonce: int,
    signed_transaction: str,
) -> None:
    async with SessionLocal() as session:
        await prepare_settlement_broadcast(
            session,
            settlement_id,
            transaction_hash=transaction_hash,
            transaction_nonce=nonce,
            signed_transaction=signed_transaction,
        )
        await session.commit()


async def save_submitted_transaction(
    settlement_id: str,
    transaction_hash: str,
) -> None:
    async with SessionLocal() as session:
        await mark_settlement_submitted(
            session,
            settlement_id,
            transaction_hash,
        )
        await session.commit()


def broadcast_or_find_existing(web3: Web3, raw_transaction: bytes, tx_hash: str) -> None:
    computed_hash = Web3.to_hex(Web3.keccak(raw_transaction)).lower()
    if computed_hash != tx_hash.lower():
        raise PaymentScriptError(
            "Stored signed transaction does not match its expected hash"
        )
    try:
        returned_hash = Web3.to_hex(web3.eth.send_raw_transaction(raw_transaction))
    except Exception as broadcast_error:
        try:
            web3.eth.get_transaction(tx_hash)
        except TransactionNotFound:
            raise PaymentScriptError(
                "Settlement broadcast was not accepted. Its reservation remains "
                "safe and the same signed transaction can be retried."
            ) from broadcast_error
        except Exception as lookup_error:
            raise PaymentScriptError(
                "Could not determine whether the settlement reached the network. "
                "Its reservation and signed transaction remain safe to retry."
            ) from lookup_error
        return

    if returned_hash.lower() != tx_hash.lower():
        raise PaymentScriptError(
            "RPC returned a different hash for the signed settlement"
        )


async def run() -> int:
    arguments = parse_arguments()
    try:
        if settings.stablepay_vault_address is None:
            raise PaymentScriptError("STABLEPAY_VAULT_ADDRESS is not configured")
        candidates = await load_candidates(arguments.limit)
        if not candidates:
            print("No pending or interrupted settlements to broadcast.")
            return 0

        account = load_vault_account(arguments.private_key_env)
        expected_address = Web3.to_checksum_address(
            settings.stablepay_vault_address
        )
        sender = Web3.to_checksum_address(account.address)
        if sender != expected_address:
            raise PaymentScriptError(
                "Private key does not control STABLEPAY_VAULT_ADDRESS"
            )

        web3 = connect_to_base_sepolia()
        usdc = web3.eth.contract(
            address=Web3.to_checksum_address(settings.base_sepolia_usdc_address),
            abi=ERC20_ABI,
        )
        pending = [
            settlement
            for settlement in candidates
            if settlement.status == SettlementStatus.PENDING
        ]
        pending_total = sum(
            (settlement.amount_atomic for settlement in pending),
            start=0,
        )
        usdc_balance = usdc.functions.balanceOf(sender).call()
        eth_balance = web3.eth.get_balance(sender)

        print("Vault address:", sender)
        print("Pending settlements:", len(pending))
        print("Pending payout total:", Decimal(pending_total) / USDC_SCALE, "USDC")
        print("Vault USDC balance:", Decimal(usdc_balance) / USDC_SCALE)
        print("Vault ETH balance:", Web3.from_wei(eth_balance, "ether"))
        for settlement in pending:
            print(
                f"  {settlement.id}: "
                f"{settlement.amount} USDC -> {settlement.destination_address}"
            )

        if pending_total > usdc_balance:
            raise PaymentScriptError(
                "Vault wallet does not have enough USDC for pending settlements"
            )
        if pending and eth_balance == 0:
            raise PaymentScriptError("Vault wallet has no Base Sepolia ETH for gas")
        if pending and not arguments.yes:
            confirmation = input("Broadcast these testnet payouts? [y/N]: ")
            if confirmation.strip().lower() not in {"y", "yes"}:
                print("Settlement broadcast cancelled. Reservations remain pending.")
                return 0

        completed = 0
        for settlement in candidates:
            if settlement.status == SettlementStatus.BROADCASTING:
                if (
                    settlement.signed_transaction is None
                    or settlement.transaction_hash is None
                ):
                    print(
                        f"Skipping {settlement.id}: signed recovery data is missing"
                    )
                    continue
                raw_transaction = HexBytes(settlement.signed_transaction)
                transaction_hash = settlement.transaction_hash
                print(f"Rebroadcasting {settlement.id}: {transaction_hash}")
            else:
                nonce = web3.eth.get_transaction_count(sender, "pending")
                transaction = build_transfer_transaction(
                    web3,
                    sender,
                    Web3.to_checksum_address(settlement.destination_address),
                    settlement.amount_atomic,
                    nonce=nonce,
                )
                signed = account.sign_transaction(transaction)
                raw_transaction = getattr(signed, "raw_transaction", None)
                if raw_transaction is None:
                    raw_transaction = signed.rawTransaction
                transaction_hash = Web3.to_hex(Web3.keccak(raw_transaction)).lower()
                await save_prepared_transaction(
                    settlement.id,
                    transaction_hash,
                    nonce,
                    Web3.to_hex(raw_transaction),
                )

            broadcast_or_find_existing(
                web3,
                raw_transaction,
                transaction_hash,
            )
            await save_submitted_transaction(settlement.id, transaction_hash)
            print(f"Submitted {settlement.id}: {transaction_hash}")
            print(f"  https://sepolia.basescan.org/tx/{transaction_hash}")
            completed += 1

        print(f"Submitted {completed} settlement(s).")
        print("StablePay will confirm them automatically after enough blocks.")
        return 0
    except (PaymentScriptError, SettlementError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
