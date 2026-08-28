from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment
from domain.payments import PaymentStatus


def test_payment_status_has_expected_values():
    assert [status.value for status in PaymentStatus] == [
        "pending",
        "confirming",
        "confirmed",
        "expired",
    ]


@pytest.mark.asyncio
async def test_invalid_payment_status_is_rejected(test_session: AsyncSession):
    payment = Payment(
        id="pay_invalid_status",
        amount=Decimal("1.00"),
        currency="USDC",
        chain="base-sepolia",
        recipient_address="0x1111111111111111111111111111111111111111",
        status="not-a-real-status",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    test_session.add(payment)

    with pytest.raises(StatementError, match="not-a-real-status"):
        await test_session.commit()
