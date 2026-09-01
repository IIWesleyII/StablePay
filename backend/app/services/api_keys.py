"""Generate and verify high-entropy merchant API keys."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from uuid import uuid4

from database.models import MerchantApiKey


API_KEY_ENVIRONMENT = "sp_test"
API_KEY_ID_PATTERN = re.compile(r"key_[0-9a-f]{32}")


class ApiKeyError(ValueError):
    """Raised when API-key data is invalid."""


@dataclass(frozen=True)
class GeneratedApiKey:
    """A database record plus plaintext shown to the merchant only once."""

    plaintext: str = field(repr=False)
    record: MerchantApiKey


def generate_merchant_api_key(
    merchant_id: str,
    name: str,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> GeneratedApiKey:
    """Generate a random API key and its safe database representation."""

    normalized_name = name.strip()
    if not normalized_name:
        raise ApiKeyError("API key name must not be empty")
    if len(normalized_name) > 100:
        raise ApiKeyError("API key name must not exceed 100 characters")

    creation_time = _as_utc(created_at or datetime.now(timezone.utc))
    expiration_time = _as_utc(expires_at) if expires_at is not None else None
    if expiration_time is not None and expiration_time <= creation_time:
        raise ApiKeyError("API key expiration must be in the future")

    key_id = f"key_{uuid4().hex}"
    random_secret = secrets.token_urlsafe(32)
    plaintext = f"{API_KEY_ENVIRONMENT}.{key_id}.{random_secret}"

    record = MerchantApiKey(
        id=key_id,
        merchant_id=merchant_id,
        name=normalized_name,
        key_prefix=plaintext[:32],
        secret_hash=hash_api_key(plaintext),
        created_at=creation_time,
        expires_at=expiration_time,
    )

    return GeneratedApiKey(
        plaintext=plaintext,
        record=record,
    )


def hash_api_key(plaintext: str) -> str:
    """Create the one-way value stored by StablePay."""

    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def verify_api_key(plaintext: str, expected_hash: str) -> bool:
    """Compare a presented API key with a stored hash in constant time."""

    return hmac.compare_digest(hash_api_key(plaintext), expected_hash)


def parse_api_key_id(plaintext: str) -> str:
    """Read the public database ID embedded in a StablePay API key."""

    parts = plaintext.split(".")
    if (
        len(parts) != 3
        or parts[0] != API_KEY_ENVIRONMENT
        or API_KEY_ID_PATTERN.fullmatch(parts[1]) is None
        or not parts[2]
    ):
        raise ApiKeyError("API key has an invalid format")

    return parts[1]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ApiKeyError("API key timestamps must include a timezone")
    return value.astimezone(timezone.utc)
