from enum import Enum


class PaymentStatus(str, Enum):
    """The allowed stages in a payment's lifecycle."""

    PENDING = "pending"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
