"""Bootstrap a local StablePay merchant and its first API key."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from sqlalchemy.exc import SQLAlchemyError


APP_DIRECTORY = Path(__file__).resolve().parent / "app"
sys.path.insert(0, str(APP_DIRECTORY))

from config import settings  # noqa: E402
from database.database import SessionLocal  # noqa: E402
from services.api_keys import ApiKeyError  # noqa: E402
from services.merchants import MerchantAccountError  # noqa: E402
from services.merchants import create_merchant_account  # noqa: E402


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a StablePay merchant and initial API key",
    )
    parser.add_argument("--name", required=True, help="Merchant display name")
    parser.add_argument(
        "--wallet-address",
        default=settings.merchant_wallet_address,
        help="Base settlement address (defaults to MERCHANT_WALLET_ADDRESS)",
    )
    parser.add_argument(
        "--webhook-url",
        default=settings.merchant_webhook_url,
        help="Webhook destination (defaults to MERCHANT_WEBHOOK_URL)",
    )
    parser.add_argument(
        "--key-name",
        default="Default",
        help="Friendly label for the initial API key",
    )
    return parser.parse_args()


async def create_from_arguments(arguments: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        try:
            created = await create_merchant_account(
                session,
                name=arguments.name,
                wallet_address=arguments.wallet_address,
                webhook_url=arguments.webhook_url,
                api_key_name=arguments.key_name,
            )
            await session.commit()
        except (MerchantAccountError, ApiKeyError) as error:
            await session.rollback()
            print(f"Error: {error}", file=sys.stderr)
            return 1
        except SQLAlchemyError:
            await session.rollback()
            print(
                "Error: merchant could not be saved; check the database and migrations",
                file=sys.stderr,
            )
            return 1

    print("Merchant created successfully")
    print(f"Merchant ID: {created.merchant.id}")
    print(f"Name: {created.merchant.name}")
    print(f"Wallet: {created.merchant.wallet_address}")
    print(f"Webhook URL: {created.merchant.webhook_url}")
    print(f"API key ID: {created.api_key_id}")
    print()
    print("API key (shown only once):")
    print(created.api_key)
    print()
    print("Store this key securely. StablePay stores only its hash.")
    return 0


def main() -> int:
    return asyncio.run(create_from_arguments(parse_arguments()))


if __name__ == "__main__":
    raise SystemExit(main())
