from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from domain.payments import PaymentStatus


class PaymentCreate(BaseModel):
    """Data required to create a payment."""

    amount: Decimal = Field(
        gt=0,
        max_digits=30,
        decimal_places=6,
    )


class PaymentResponse(BaseModel):
    """Payment data returned by the API."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    amount: Decimal
    currency: str
    chain: str
    recipient_address: str
    status: PaymentStatus
    transaction_hash: str | None
    created_at: datetime
    expires_at: datetime
    detected_at: datetime | None
    confirmed_at: datetime | None


class PaymentVerificationRequest(BaseModel):
    """A blockchain transaction submitted as proof of payment."""

    transaction_hash: str = Field(
        pattern=r"^0x[0-9a-fA-F]{64}$",
    )

    @field_validator("transaction_hash")
    @classmethod
    def normalize_transaction_hash(cls, value: str) -> str:
        return value.lower()


class PaymentVerificationResponse(BaseModel):
    """The matched transfer and resulting payment state."""

    payment: PaymentResponse
    sender_address: str
    confirmations: int
    required_confirmations: int
