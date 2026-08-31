from collections.abc import Sequence

from blockchain.base import UsdcTransfer
from database.models import Payment


class PaymentVerificationError(ValueError):
    """Raised when a transaction does not satisfy a payment request."""


def find_matching_transfer(
    payment: Payment,
    transfers: Sequence[UsdcTransfer],
) -> UsdcTransfer:
    """Find the exact recipient and amount required by a payment."""

    if not transfers:
        raise PaymentVerificationError(
            "Transaction does not contain a USDC transfer"
        )

    recipient_transfers = [
        transfer
        for transfer in transfers
        if transfer.recipient.lower() == payment.recipient_address.lower()
    ]

    if not recipient_transfers:
        raise PaymentVerificationError(
            "USDC transfer recipient does not match this payment"
        )

    for transfer in recipient_transfers:
        if transfer.amount == payment.amount:
            return transfer

    raise PaymentVerificationError(
        "USDC transfer amount does not match this payment"
    )
