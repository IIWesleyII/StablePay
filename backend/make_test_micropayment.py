"""Make and verify one internal StablePay micropayment."""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values

from send_test_payment import DEFAULT_API_URL
from send_test_payment import PaymentScriptError
from send_test_payment import request_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Make one internal USDC micropayment and verify its retry",
    )
    parser.add_argument(
        "amount",
        nargs="?",
        default="0.001",
        help="USDC amount (default: 0.001)",
    )
    parser.add_argument(
        "--reference",
        default="micropayment-demo",
        help="Merchant-facing purchase reference",
    )
    parser.add_argument(
        "--idempotency-key",
        help="Reuse this only when retrying the same logical purchase",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"StablePay API URL (default: {DEFAULT_API_URL})",
    )
    return parser.parse_args()


def load_secret(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        value = dotenv_values(PROJECT_ROOT / ".env").get(name)
    if not value:
        raise PaymentScriptError(f"{name} is not configured")
    return str(value).strip()


def main() -> int:
    arguments = parse_arguments()
    api_url = arguments.api_url.rstrip("/")
    try:
        try:
            amount = Decimal(arguments.amount)
        except InvalidOperation as error:
            raise PaymentScriptError("Amount is not a valid number") from error
        atomic_amount = amount * 1_000_000
        if amount <= 0 or atomic_amount != atomic_amount.to_integral_value():
            raise PaymentScriptError(
                "Amount must be positive with at most six decimal places"
            )

        merchant_key = load_secret("STABLEPAY_API_KEY")
        vault_token = load_secret("STABLEPAY_VAULT_TOKEN")
        merchant_headers = {"Authorization": f"Bearer {merchant_key}"}
        vault_headers = {"Authorization": f"Bearer {vault_token}"}
        merchant = request_json(
            "GET",
            f"{api_url}/merchants/me",
            headers=merchant_headers,
        )

        idempotency_key = (
            arguments.idempotency_key
            or f"micropayment-demo-{uuid4().hex}"
        )
        payment_headers = {
            **vault_headers,
            "Idempotency-Key": idempotency_key,
        }
        payload = {
            "merchant_id": merchant["id"],
            "amount": str(amount),
            "reference": arguments.reference,
        }
        first = request_json(
            "POST",
            f"{api_url}/vaults/micropayments",
            payload,
            payment_headers,
        )
        retry = request_json(
            "POST",
            f"{api_url}/vaults/micropayments",
            payload,
            payment_headers,
        )
        vault = request_json(
            "GET",
            f"{api_url}/vaults/me",
            headers=vault_headers,
        )
        merchant_balance = request_json(
            "GET",
            f"{api_url}/merchants/me/balance",
            headers=merchant_headers,
        )
        receipt = request_json(
            "GET",
            f"{api_url}/merchants/me/micropayments/{first['id']}",
            headers=merchant_headers,
        )

        if first["id"] != retry["id"] or retry["replayed"] is not True:
            raise PaymentScriptError("Idempotent retry created another payment")
        if receipt["id"] != first["id"]:
            raise PaymentScriptError("Merchant could not verify the receipt")

        print("Micropayment confirmed:", first["id"])
        print("Amount:", first["amount"], first["currency"])
        print("First request replayed:", first["replayed"])
        print("Identical retry replayed:", retry["replayed"])
        print("Vault balance:", vault["balance"], vault["currency"])
        print(
            "Merchant balance:",
            merchant_balance["available_balance"],
            merchant_balance["currency"],
        )
        print("Merchant receipt verified: yes")
        return 0
    except PaymentScriptError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
