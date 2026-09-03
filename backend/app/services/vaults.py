"""Wallet ownership proof and reusable vault access credentials."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import uuid4

from eth_account import Account
from eth_account.messages import encode_defunct
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from web3 import Web3

from config import settings
from database.models import Vault
from database.models import VaultChallenge


class VaultError(ValueError):
    """Raised when wallet proof or vault credentials are invalid."""


@dataclass(frozen=True)
class CreatedVault:
    vault: Vault
    access_token: str


async def create_vault_challenge(
    session: AsyncSession,
    wallet_address: str,
    current_time: datetime | None = None,
) -> VaultChallenge:
    now = _as_utc(current_time or datetime.now(timezone.utc))
    wallet = _checksum_address(wallet_address)
    expires_at = now + timedelta(
        minutes=settings.vault_challenge_expiration_minutes
    )
    nonce = secrets.token_hex(16)
    message = (
        "StablePay vault authorization\n"
        "Network: Base Sepolia\n"
        f"Wallet: {wallet}\n"
        f"Nonce: {nonce}\n"
        f"Expires at: {expires_at.isoformat()}\n\n"
        "Signing is free and does not send a blockchain transaction."
    )
    challenge = VaultChallenge(
        id=f"vch_{uuid4().hex}",
        wallet_address=wallet,
        message=message,
        expires_at=expires_at,
        used_at=None,
        created_at=now,
    )
    session.add(challenge)
    await session.flush()
    return challenge


async def create_vault_from_signature(
    session: AsyncSession,
    challenge_id: str,
    signature: str,
    current_time: datetime | None = None,
) -> CreatedVault:
    now = _as_utc(current_time or datetime.now(timezone.utc))
    result = await session.execute(
        select(VaultChallenge)
        .where(VaultChallenge.id == challenge_id)
        .with_for_update()
    )
    challenge = result.scalar_one_or_none()
    if challenge is None:
        raise VaultError("Vault challenge was not found")
    if challenge.used_at is not None:
        raise VaultError("Vault challenge has already been used")
    if now >= _as_utc(challenge.expires_at):
        raise VaultError("Vault challenge has expired")

    try:
        recovered = Account.recover_message(
            encode_defunct(text=challenge.message),
            signature=signature,
        )
    except Exception as error:
        raise VaultError("Vault signature is invalid") from error
    if recovered.lower() != challenge.wallet_address.lower():
        raise VaultError("Vault signature does not match the requested wallet")

    existing_result = await session.execute(
        select(Vault.id).where(
            func.lower(Vault.wallet_address) == challenge.wallet_address.lower()
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        raise VaultError("A vault already exists for this wallet")

    vault_id = f"vlt_{uuid4().hex}"
    secret = secrets.token_urlsafe(32)
    plaintext = f"{vault_id}.{secret}"
    vault = Vault(
        id=vault_id,
        wallet_address=challenge.wallet_address,
        access_token_prefix=plaintext[:20],
        access_token_hash=_hash_token(plaintext),
        is_active=True,
        created_at=now,
    )
    challenge.used_at = now
    session.add(vault)
    await session.flush()
    return CreatedVault(vault=vault, access_token=plaintext)


def parse_vault_id(access_token: str) -> str:
    vault_id, separator, secret = access_token.partition(".")
    if (
        separator != "."
        or not vault_id.startswith("vlt_")
        or len(vault_id) != 36
        or not secret
    ):
        raise VaultError("Vault access token is malformed")
    return vault_id


def verify_vault_access_token(access_token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(_hash_token(access_token), expected_hash)


def _hash_token(access_token: str) -> str:
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()


def _checksum_address(value: str) -> str:
    try:
        return Web3.to_checksum_address(value)
    except (TypeError, ValueError) as error:
        raise VaultError("Vault wallet must be a valid Ethereum address") from error


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite drops timezone metadata in tests; database timestamps are UTC.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
