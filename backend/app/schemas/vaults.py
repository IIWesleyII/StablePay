"""Request and response models for customer vaults and micropayments."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator

from domain.ledger import LedgerTransactionType
from domain.ledger import VaultDepositStatus


class VaultChallengeCreate(BaseModel):
    wallet_address: str = Field(min_length=42, max_length=42)


class VaultChallengeResponse(BaseModel):
    id: str
    wallet_address: str
    message: str
    expires_at: datetime


class VaultCreate(BaseModel):
    challenge_id: str = Field(min_length=1, max_length=40)
    signature: str = Field(pattern=r"^(0x)?[0-9a-fA-F]{130}$")


class VaultCreatedResponse(BaseModel):
    id: str
    wallet_address: str
    access_token: str
    balance: Decimal
    currency: str
    created_at: datetime


class VaultResponse(BaseModel):
    id: str
    wallet_address: str
    balance: Decimal
    currency: str
    created_at: datetime


class VaultDepositCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=30, decimal_places=6)


class VaultDepositResponse(BaseModel):
    id: str
    vault_id: str
    amount: Decimal
    currency: str
    chain: str
    recipient_address: str
    sender_address: str
    status: VaultDepositStatus
    transaction_hash: str | None
    transaction_block_number: int | None
    transaction_log_index: int | None
    created_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None


class MicropaymentCreate(BaseModel):
    merchant_id: str = Field(min_length=1, max_length=40)
    amount: Decimal = Field(gt=0, max_digits=30, decimal_places=6)
    reference: str = Field(min_length=1, max_length=100)

    @field_validator("merchant_id", "reference")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be empty")
        return normalized


class MicropaymentResponse(BaseModel):
    id: str
    transaction_type: LedgerTransactionType
    merchant_id: str
    amount: Decimal
    currency: str
    reference: str
    vault_balance: Decimal
    replayed: bool
    created_at: datetime


class LedgerActivityResponse(BaseModel):
    id: str
    transaction_type: LedgerTransactionType
    amount: Decimal
    reference: str
    created_at: datetime
