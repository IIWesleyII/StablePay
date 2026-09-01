"""Create merchant accounts and their initial API credentials."""

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from database.models import Merchant
from services.api_keys import GeneratedApiKey
from services.api_keys import generate_merchant_api_key


class MerchantAccountError(ValueError):
    """Raised when a merchant account cannot be safely created."""


class DuplicateMerchantWalletError(MerchantAccountError):
    """Raised when a wallet already belongs to another merchant."""


@dataclass(frozen=True)
class CreatedMerchantAccount:
    """A new merchant plus the API key displayed only during creation."""

    merchant: Merchant
    api_key: str = field(repr=False)
    api_key_id: str


async def create_merchant_account(
    session: AsyncSession,
    name: str,
    wallet_address: str,
    webhook_url: str,
    api_key_name: str = "Default",
    created_at: datetime | None = None,
) -> CreatedMerchantAccount:
    """Stage one merchant and initial key without committing the session."""

    merchant_name = _validate_name(name)
    normalized_wallet = _validate_wallet_address(wallet_address)
    normalized_webhook_url = _validate_webhook_url(webhook_url)
    creation_time = _as_utc(created_at or datetime.now(timezone.utc))

    duplicate_result = await session.execute(
        select(Merchant.id).where(
            func.lower(Merchant.wallet_address) == normalized_wallet.lower()
        )
    )
    if duplicate_result.scalar_one_or_none() is not None:
        raise DuplicateMerchantWalletError(
            "A merchant already exists for this wallet address"
        )

    merchant = Merchant(
        id=f"mch_{uuid4().hex}",
        name=merchant_name,
        wallet_address=normalized_wallet,
        webhook_url=normalized_webhook_url,
        is_active=True,
        created_at=creation_time,
        updated_at=creation_time,
    )
    generated_key: GeneratedApiKey = generate_merchant_api_key(
        merchant.id,
        api_key_name,
        creation_time,
    )

    session.add_all([merchant, generated_key.record])
    await session.flush()

    return CreatedMerchantAccount(
        merchant=merchant,
        api_key=generated_key.plaintext,
        api_key_id=generated_key.record.id,
    )


async def update_merchant_account(
    session: AsyncSession,
    merchant: Merchant,
    *,
    name: str | None = None,
    wallet_address: str | None = None,
    webhook_url: str | None = None,
    updated_at: datetime | None = None,
) -> Merchant:
    """Validate and stage changes to an existing merchant account."""

    if name is not None:
        merchant.name = _validate_name(name)

    if wallet_address is not None:
        normalized_wallet = _validate_wallet_address(wallet_address)
        duplicate_result = await session.execute(
            select(Merchant.id).where(
                func.lower(Merchant.wallet_address) == normalized_wallet.lower(),
                Merchant.id != merchant.id,
            )
        )
        if duplicate_result.scalar_one_or_none() is not None:
            raise DuplicateMerchantWalletError(
                "A merchant already exists for this wallet address"
            )
        merchant.wallet_address = normalized_wallet

    if webhook_url is not None:
        merchant.webhook_url = _validate_webhook_url(webhook_url)

    merchant.updated_at = _as_utc(updated_at or datetime.now(timezone.utc))
    await session.flush()
    return merchant


def _validate_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise MerchantAccountError("Merchant name must not be empty")
    if len(normalized) > 100:
        raise MerchantAccountError("Merchant name must not exceed 100 characters")
    return normalized


def _validate_wallet_address(value: str) -> str:
    try:
        return Web3.to_checksum_address(value)
    except (TypeError, ValueError) as error:
        raise MerchantAccountError(
            "Merchant wallet address must be a valid Ethereum address"
        ) from error


def _validate_webhook_url(value: str) -> str:
    normalized = value.strip()
    parsed_url = urlparse(normalized)

    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise MerchantAccountError("Merchant webhook URL must be valid HTTP(S)")
    if parsed_url.username is not None or parsed_url.password is not None:
        raise MerchantAccountError("Merchant webhook URL must not contain credentials")
    if parsed_url.fragment:
        raise MerchantAccountError("Merchant webhook URL must not contain a fragment")
    if len(normalized) > 2048:
        raise MerchantAccountError(
            "Merchant webhook URL must not exceed 2048 characters"
        )

    return normalized


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MerchantAccountError(
            "Merchant creation timestamp must include a timezone"
        )
    return value.astimezone(timezone.utc)
