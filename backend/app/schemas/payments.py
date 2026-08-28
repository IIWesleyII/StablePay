from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

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
