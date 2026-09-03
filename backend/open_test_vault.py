"""Prove ownership of a test wallet and open a StablePay vault."""

from __future__ import annotations

import argparse

from eth_account.messages import encode_defunct

from send_test_payment import DEFAULT_API_URL
from send_test_payment import PaymentScriptError
from send_test_payment import load_test_account
from send_test_payment import request_json


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open a StablePay vault using one free wallet signature",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"StablePay API URL (default: {DEFAULT_API_URL})",
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
        account = load_test_account(arguments.private_key_env)
        challenge = request_json(
            "POST",
            f"{api_url}/vaults/challenges",
            {"wallet_address": account.address},
        )
        if challenge["wallet_address"].lower() != account.address.lower():
            raise PaymentScriptError("StablePay returned a challenge for another wallet")

        signature = account.sign_message(
            encode_defunct(text=challenge["message"])
        ).signature.hex()
        vault = request_json(
            "POST",
            f"{api_url}/vaults",
            {
                "challenge_id": challenge["id"],
                "signature": signature,
            },
        )

        print("Vault opened:", vault["id"])
        print("Wallet:", vault["wallet_address"])
        print("Initial balance:", vault["balance"], vault["currency"])
        print("Vault access token (shown once):", vault["access_token"])
        print("Save it as STABLEPAY_VAULT_TOKEN in your local .env file.")
        return 0
    except PaymentScriptError as error:
        print(f"Error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
