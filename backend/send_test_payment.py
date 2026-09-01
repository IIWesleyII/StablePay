"""Send and verify a Base Sepolia USDC payment from a test wallet."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen

from eth_account import Account
from web3 import Web3


APP_DIRECTORY = Path(__file__).resolve().parent / "app"
sys.path.insert(0, str(APP_DIRECTORY))

from config import settings  # noqa: E402


BASE_SEPOLIA_CHAIN_ID = 84532
USDC_DECIMALS = 6
USDC_SCALE = Decimal(10**USDC_DECIMALS)
DEFAULT_API_URL = "http://127.0.0.1:8000"

ERC20_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "recipient", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


class PaymentScriptError(RuntimeError):
    """Raised when the test payment cannot be safely completed."""


def request_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a JSON request to the local StablePay API."""

    encoded_body = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=encoded_body,
        method=method,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        response_text = error.read().decode("utf-8")
        try:
            detail = json.loads(response_text).get("detail", response_text)
        except json.JSONDecodeError:
            detail = response_text
        raise PaymentScriptError(
            f"StablePay returned HTTP {error.code}: {detail}"
        ) from error
    except URLError as error:
        raise PaymentScriptError(
            "Could not connect to StablePay. Make sure the API is running."
        ) from error


def validate_payment(payment: dict[str, Any]) -> tuple[Decimal, str]:
    """Validate that a payment is safe for this Base Sepolia test script."""

    if payment.get("status") != "pending":
        raise PaymentScriptError(
            f"Payment status is {payment.get('status')!r}; expected 'pending'. "
            "This prevents accidentally sending the payment twice."
        )

    if payment.get("currency") != "USDC":
        raise PaymentScriptError("This script only sends USDC payments")

    if payment.get("chain") != "base-sepolia":
        raise PaymentScriptError("This script only sends on Base Sepolia")

    expiration = datetime.fromisoformat(
        str(payment["expires_at"]).replace("Z", "+00:00")
    )
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expiration.astimezone(timezone.utc):
        raise PaymentScriptError("Payment has already expired")

    amount = Decimal(str(payment["amount"]))
    raw_amount = amount * USDC_SCALE
    if raw_amount != raw_amount.to_integral_value():
        raise PaymentScriptError("USDC amount has more than six decimal places")

    try:
        recipient = Web3.to_checksum_address(payment["recipient_address"])
    except (TypeError, ValueError) as error:
        raise PaymentScriptError("Payment contains an invalid recipient address") from error

    return amount, recipient


def load_test_account(private_key_env: str | None = None):
    """Load a test key from a named environment variable or hidden prompt."""

    if private_key_env is None:
        private_key = getpass.getpass("Test wallet private key (hidden): ").strip()
    else:
        private_key = os.environ.get(private_key_env, "").strip()
        if not private_key:
            raise PaymentScriptError(
                f"Environment variable {private_key_env} is not configured"
            )
    try:
        return Account.from_key(private_key)
    except (TypeError, ValueError) as error:
        raise PaymentScriptError("The entered private key is invalid") from error
    finally:
        private_key = ""


def connect_to_base_sepolia() -> Web3:
    """Create a synchronous client and reject the wrong network."""

    web3 = Web3(
        Web3.HTTPProvider(
            settings.base_sepolia_rpc_url,
            request_kwargs={"timeout": 15},
        )
    )

    if not web3.is_connected():
        raise PaymentScriptError("Could not connect to the Base Sepolia RPC")

    chain_id = web3.eth.chain_id
    if chain_id != BASE_SEPOLIA_CHAIN_ID:
        raise PaymentScriptError(
            f"RPC is connected to chain {chain_id}; expected Base Sepolia "
            f"chain {BASE_SEPOLIA_CHAIN_ID}"
        )

    return web3


def build_transfer_transaction(
    web3: Web3,
    sender: str,
    recipient: str,
    raw_amount: int,
) -> dict[str, Any]:
    """Build an ERC-20 transfer using current network fee information."""

    contract = web3.eth.contract(
        address=Web3.to_checksum_address(settings.base_sepolia_usdc_address),
        abi=ERC20_ABI,
    )
    transfer = contract.functions.transfer(recipient, raw_amount)
    transaction: dict[str, Any] = {
        "from": sender,
        "chainId": BASE_SEPOLIA_CHAIN_ID,
        "nonce": web3.eth.get_transaction_count(sender, "pending"),
    }

    latest_block = web3.eth.get_block("latest")
    base_fee = latest_block.get("baseFeePerGas")
    if base_fee is None:
        transaction["gasPrice"] = web3.eth.gas_price
    else:
        priority_fee = web3.eth.max_priority_fee
        transaction["maxPriorityFeePerGas"] = priority_fee
        transaction["maxFeePerGas"] = (base_fee * 2) + priority_fee

    estimated_gas = transfer.estimate_gas({"from": sender})
    transaction["gas"] = (estimated_gas * 120) // 100

    return transfer.build_transaction(transaction)


def wait_for_stablepay_confirmation(
    api_url: str,
    payment_id: str,
    transaction_hash: str,
) -> dict[str, Any]:
    """Submit proof and briefly poll until StablePay confirms the payment."""

    verification_url = f"{api_url}/payments/{payment_id}/verify"
    deadline = time.monotonic() + 60

    while True:
        result = request_json(
            "POST",
            verification_url,
            {"transaction_hash": transaction_hash},
        )
        status = result["payment"]["status"]
        confirmations = result["confirmations"]
        required = result["required_confirmations"]
        print(
            f"StablePay status: {status} "
            f"({confirmations}/{required} confirmations)"
        )

        if status == "confirmed" or time.monotonic() >= deadline:
            return result

        time.sleep(2)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pay a StablePay request with Base Sepolia test USDC",
    )
    parser.add_argument("payment_id", help="StablePay payment ID, such as pay_abc123")
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"StablePay API URL (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the final interactive broadcast confirmation",
    )
    parser.add_argument(
        "--private-key-env",
        help="Read the test key from this environment variable instead of prompting",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    api_url = arguments.api_url.rstrip("/")

    try:
        payment = request_json(
            "GET",
            f"{api_url}/payments/{arguments.payment_id}",
        )
        amount, recipient = validate_payment(payment)
        raw_amount = int(amount * USDC_SCALE)
        account = load_test_account(arguments.private_key_env)
        sender = Web3.to_checksum_address(account.address)

        if sender == recipient:
            raise PaymentScriptError(
                "The test wallet and payment recipient are the same address"
            )

        web3 = connect_to_base_sepolia()
        usdc = web3.eth.contract(
            address=Web3.to_checksum_address(settings.base_sepolia_usdc_address),
            abi=ERC20_ABI,
        )
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
            confirmation = input("Broadcast this testnet transfer? [y/N]: ")
            if confirmation.strip().lower() not in {"y", "yes"}:
                print("Transfer cancelled. No transaction was sent.")
                return 0

        transaction = build_transfer_transaction(
            web3,
            sender,
            recipient,
            raw_amount,
        )
        signed_transaction = account.sign_transaction(transaction)
        raw_transaction = getattr(signed_transaction, "raw_transaction", None)
        if raw_transaction is None:
            raw_transaction = signed_transaction.rawTransaction

        transaction_hash_bytes = web3.eth.send_raw_transaction(raw_transaction)
        transaction_hash = Web3.to_hex(transaction_hash_bytes).lower()
        print(f"Transaction submitted: {transaction_hash}")
        print(f"Explorer: https://sepolia.basescan.org/tx/{transaction_hash}")

        receipt = web3.eth.wait_for_transaction_receipt(
            transaction_hash_bytes,
            timeout=120,
            poll_latency=2,
        )
        if receipt["status"] != 1:
            raise PaymentScriptError("Transaction was mined but reverted")

        result = wait_for_stablepay_confirmation(
            api_url,
            arguments.payment_id,
            transaction_hash,
        )
        if result["payment"]["status"] != "confirmed":
            print(
                "The transaction succeeded but StablePay still needs more "
                "confirmations. Re-submit the same hash to the verify endpoint."
            )
        return 0
    except PaymentScriptError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
