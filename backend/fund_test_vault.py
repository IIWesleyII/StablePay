"""Create and send a Base Sepolia test-USDC vault deposit."""

from __future__ import annotations

import argparse
import os
import time
from decimal import Decimal
from decimal import InvalidOperation

from web3 import Web3

from send_test_payment import DEFAULT_API_URL
from send_test_payment import ERC20_ABI
from send_test_payment import PaymentScriptError
from send_test_payment import USDC_SCALE
from send_test_payment import build_transfer_transaction
from send_test_payment import connect_to_base_sepolia
from send_test_payment import load_test_account
from send_test_payment import request_json
from send_test_payment import settings


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fund a StablePay vault with Base Sepolia test USDC",
    )
    parser.add_argument("amount", help="Exact USDC amount, such as 5 or 0.01")
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"StablePay API URL (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--private-key-env",
        help="Read the test key from this environment variable instead of prompting",
    )
    parser.add_argument(
        "--vault-token-env",
        default="STABLEPAY_VAULT_TOKEN",
        help="Environment variable containing the reusable vault token",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the final interactive broadcast confirmation",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    api_url = arguments.api_url.rstrip("/")
    try:
        try:
            amount = Decimal(arguments.amount)
        except InvalidOperation as error:
            raise PaymentScriptError("Deposit amount is not a valid number") from error
        raw_amount_decimal = amount * USDC_SCALE
        if amount <= 0 or raw_amount_decimal != raw_amount_decimal.to_integral_value():
            raise PaymentScriptError(
                "Deposit must be positive with at most six decimal places"
            )

        vault_token = os.environ.get(arguments.vault_token_env, "").strip()
        if not vault_token:
            raise PaymentScriptError(
                f"Environment variable {arguments.vault_token_env} is not configured"
            )
        headers = {"Authorization": f"Bearer {vault_token}"}
        deposit = request_json(
            "POST",
            f"{api_url}/vaults/deposits",
            {"amount": str(amount)},
            headers=headers,
        )
        account = load_test_account(arguments.private_key_env)
        sender = Web3.to_checksum_address(account.address)
        expected_sender = Web3.to_checksum_address(deposit["sender_address"])
        recipient = Web3.to_checksum_address(deposit["recipient_address"])
        if sender != expected_sender:
            raise PaymentScriptError(
                "Private key does not belong to the wallet that opened this vault"
            )
        if sender == recipient:
            raise PaymentScriptError("Deposit sender and vault address must differ")

        web3 = connect_to_base_sepolia()
        usdc = web3.eth.contract(
            address=Web3.to_checksum_address(settings.base_sepolia_usdc_address),
            abi=ERC20_ABI,
        )
        raw_amount = int(raw_amount_decimal)
        usdc_balance = usdc.functions.balanceOf(sender).call()
        eth_balance = web3.eth.get_balance(sender)

        print(f"Sender:    {sender}")
        print(f"Recipient: {recipient}")
        print(f"Amount:    {amount} USDC")
        print(f"USDC balance: {Decimal(usdc_balance) / USDC_SCALE}")
        print(f"ETH balance:  {Web3.from_wei(eth_balance, 'ether')}")
        if usdc_balance < raw_amount:
            raise PaymentScriptError("Test wallet does not have enough USDC")
        if eth_balance == 0:
            raise PaymentScriptError("Test wallet has no Base Sepolia ETH for gas")

        if not arguments.yes:
            confirmation = input("Broadcast this vault deposit? [y/N]: ")
            if confirmation.strip().lower() not in {"y", "yes"}:
                print("Transfer cancelled. No transaction was sent.")
                return 0

        transaction = build_transfer_transaction(
            web3, sender, recipient, raw_amount
        )
        signed = account.sign_transaction(transaction)
        raw_transaction = getattr(signed, "raw_transaction", None)
        if raw_transaction is None:
            raw_transaction = signed.rawTransaction
        transaction_hash_bytes = web3.eth.send_raw_transaction(raw_transaction)
        transaction_hash = Web3.to_hex(transaction_hash_bytes).lower()
        print("Transaction submitted:", transaction_hash)
        print(f"Explorer: https://sepolia.basescan.org/tx/{transaction_hash}")

        receipt = web3.eth.wait_for_transaction_receipt(
            transaction_hash_bytes, timeout=120, poll_latency=2
        )
        if receipt["status"] != 1:
            raise PaymentScriptError("Transaction was mined but reverted")

        deadline = time.monotonic() + 180
        previous_status = None
        while True:
            current = request_json(
                "GET",
                f"{api_url}/vaults/deposits/{deposit['id']}",
                headers=headers,
            )
            if current["status"] != previous_status:
                print("StablePay deposit status:", current["status"])
                previous_status = current["status"]
            if current["status"] == "confirmed":
                print("Vault balance credited. Internal micropayments are ready.")
                return 0
            if time.monotonic() >= deadline:
                print("Transfer succeeded; StablePay is still monitoring it.")
                return 0
            time.sleep(2)
    except PaymentScriptError as error:
        print(f"Error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
