from datetime import datetime
from datetime import timezone
from decimal import Decimal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from domain.ledger import LedgerTransactionType


class MerchantResponse(BaseModel):
    """Public merchant account data returned to that merchant."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    wallet_address: str
    webhook_url: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class MerchantUpdate(BaseModel):
    """Merchant account fields that may be changed independently."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    wallet_address: str | None = Field(default=None, min_length=1, max_length=42)
    webhook_url: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("name", "wallet_address", "webhook_url")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_at_least_one_non_null_change(self):
        changes = self.model_dump(exclude_unset=True)
        if not changes:
            raise ValueError("At least one merchant field must be provided")
        if any(value is None for value in changes.values()):
            raise ValueError("Merchant fields cannot be null")
        return self


class MerchantApiKeyCreate(BaseModel):
    """Data required to create another merchant API key."""

    name: str = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("API key name must not be empty")
        return normalized

    @field_validator("expires_at")
    @classmethod
    def validate_expiration_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("API key expiration must include a timezone")
        return value.astimezone(timezone.utc)


class MerchantApiKeyResponse(BaseModel):
    """Safe API-key metadata that never contains the key secret or hash."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class MerchantApiKeyCreatedResponse(MerchantApiKeyResponse):
    """A newly created key whose plaintext is returned exactly once."""

    api_key: str


class MerchantBalanceResponse(BaseModel):
    """Internal USDC owed to a merchant before blockchain settlement."""

    merchant_id: str
    available_balance: Decimal
    currency: str


class MerchantMicropaymentResponse(BaseModel):
    """A receipt the destination merchant can independently verify."""

    id: str
    transaction_type: LedgerTransactionType
    amount: Decimal
    currency: str
    reference: str
    created_at: datetime
