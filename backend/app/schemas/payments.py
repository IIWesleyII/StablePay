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
    merchant_id: str | None
    amount: Decimal
    currency: str
    chain: str
    recipient_address: str
    status: PaymentStatus
    transaction_hash: str | None
    payer_address: str | None
    transaction_block_number: int | None
    transaction_log_index: int | None
    created_at: datetime
    expires_at: datetime
    detected_at: datetime | None
    confirmed_at: datetime | None


class PaymentStatusCounts(BaseModel):
    """Merchant payment totals grouped by lifecycle status."""

    pending: int
    confirming: int
    confirmed: int
    expired: int


class PaymentListResponse(BaseModel):
    """A page of merchant payments plus unfiltered status totals."""

    items: list[PaymentResponse]
    total: int
    limit: int
    offset: int
    status_counts: PaymentStatusCounts


class CheckoutPaymentResponse(BaseModel):
    """Public payment details required by the customer checkout page."""

    id: str
    merchant_name: str
    amount: Decimal
    currency: str
    chain: str
    recipient_address: str
    status: PaymentStatus
    transaction_hash: str | None
    expires_at: datetime
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
